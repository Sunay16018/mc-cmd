import os
from datetime import datetime
import json
import re
import logging
import time
import hashlib
import secrets
from functools import wraps

from flask import Flask, request, jsonify, render_template, send_from_directory, Response
from flask_cors import CORS

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Logging Ayarı ────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/generate": {"origins": "*"}, r"/ping": {"origins": "*"}})

# ── Cerebras API Ayarları ────────────────────────────────────
API_URL = "https://api.cerebras.ai/v1/chat/completions"
MODEL = "gpt-oss-120b"

# GPT OSS 120B temperature yapılandırılamaz - kaldırıldı
MAX_TOKENS = 3000
REQUEST_TIMEOUT = (15, 120)  # (connect, read)

# ── Çoklu API Key Yönetimi ───────────────────────────────────
def get_api_keys():
    """CEREBRAS_API_KEY_1'den CEREBRAS_API_KEY_6'ya kadar olan key'leri sırayla alır."""
    keys = []
    for i in range(1, 7):
        key = os.environ.get(f"CEREBRAS_API_KEY_{i}")
        if key:
            keys.append(key)
    
    # Eski tek key formatını da destekle (geriye uyumluluk)
    legacy_key = os.environ.get("CEREBRAS_API_KEY")
    if legacy_key and legacy_key not in keys:
        keys.insert(0, legacy_key)
    
    return keys

API_KEYS = get_api_keys()
current_key_index = 0

# ── API Key Kontrolü ─────────────────────────────────────────
if not API_KEYS:
    logger.warning("CEREBRAS_API_KEY_1..6 ortam değişkenleri ayarlanmamış! API çağrıları başarısız olacak.")
else:
    logger.info(f"{len(API_KEYS)} adet API key yüklendi.")

# ── Yardımcı Fonksiyonlar ────────────────────────────────────
def get_current_api_key():
    """Şu anki aktif API key'i döndürür."""
    global current_key_index
    if not API_KEYS:
        return None
    return API_KEYS[current_key_index % len(API_KEYS)]

def rotate_api_key():
    """Bir sonraki API key'e geçer."""
    global current_key_index
    if len(API_KEYS) > 1:
        current_key_index = (current_key_index + 1) % len(API_KEYS)
        logger.info(f"API key rotasyonu: Key #{current_key_index + 1} aktif.")

def get_all_api_keys_with_index():
    """Tüm key'leri ve index'lerini döndürür (retry için)."""
    if not API_KEYS:
        return []
    # Mevcut key'den başlayarak sıralı liste oluştur
    result = []
    for i in range(len(API_KEYS)):
        idx = (current_key_index + i) % len(API_KEYS)
        result.append((idx, API_KEYS[idx]))
    return result

# ── Retry Session ────────────────────────────────────────────
def get_retry_session(
    retries=3,
    backoff_factor=1,
    status_forcelist=(500, 502, 503, 504),
    allowed_methods=("POST", "GET")
):
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=allowed_methods,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session

# ── Cerebras API Çağrısı (Çoklu Key Desteği) ─────────────────
def call_cerebras_api(messages, stream=False):
    """
    Cerebras API'ye çağrı yapar. Başarısız olursa sıradaki key ile dener.
    Tüm key'ler başarısız olursa exception fırlatır.
    """
    if not API_KEYS:
        raise Exception("Hiç API key tanımlı değil!")
    
    headers_base = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "stream": stream
    }
    
    # Her key için deneme
    keys_to_try = get_all_api_keys_with_index()
    last_exception = None
    
    for key_idx, api_key in keys_to_try:
        headers = {**headers_base, "Authorization": f"Bearer {api_key}"}
        
        try:
            session = get_retry_session()
            
            logger.info(f"API çağrısı deneniyor (Key #{key_idx + 1})...")
            
            response = session.post(
                API_URL,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                stream=stream
            )
            
            # Rate limit veya auth hatası kontrolü
            if response.status_code in (401, 403, 429):
                logger.warning(f"Key #{key_idx + 1} başarısız (HTTP {response.status_code}): {response.text[:200]}")
                last_exception = Exception(f"Key #{key_idx + 1} HTTP {response.status_code}: {response.text[:200]}")
                continue  # Sonraki key'e geç
            
            response.raise_for_status()
            
            # Başarılı! Mevcut key index'ini güncelle
            global current_key_index
            current_key_index = key_idx
            logger.info(f"Key #{key_idx + 1} ile başarılı yanıt alındı.")
            
            return response
            
        except requests.exceptions.Timeout as e:
            logger.warning(f"Key #{key_idx + 1} zaman aşımı: {str(e)}")
            last_exception = e
            continue
            
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Key #{key_idx + 1} bağlantı hatası: {str(e)}")
            last_exception = e
            continue
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code >= 500:
                logger.warning(f"Key #{key_idx + 1} sunucu hatası: {e.response.status_code}")
                last_exception = e
                continue
            raise  # 4xx hatalarında (client hatası) hemen fırlat
            
        except Exception as e:
            logger.warning(f"Key #{key_idx + 1} beklenmeyen hata: {str(e)}")
            last_exception = e
            continue
    
    # Tüm key'ler başarısız
    raise Exception(f"Tüm {len(API_KEYS)} API key başarısız oldu. Son hata: {str(last_exception)}")


# ── Sürüm Listesi ────────────────────────────────────────────
VERSIONS = [
    "1.21.11","1.21.8","1.21.5","1.21.4","1.21.1","1.21",
    "1.20.6","1.20.5","1.20.4","1.20.2","1.20.1","1.20",
    "1.19.4","1.19.3","1.19.2","1.19.1","1.19",
    "1.18.2","1.18.1","1.18",
    "1.17.1","1.17",
    "1.16.5","1.16.4","1.16.3","1.16.2","1.16.1","1.16",
    "1.15.2","1.15","1.14.4","1.14",
    "1.13.2","1.13",
    "1.12.2","1.12","1.11.2","1.11","1.10.2","1.10",
    "1.9.4","1.9","1.8.9","1.8"
]

# ── Komut Türleri (Emoji Yok) ────────────────────────────────
COMMAND_TYPES = [
    {"value":"all",       "label":"Tümü (Otomatik Seç)"},
    {"value":"give",      "label":"Eşya Verme (/give)"},
    {"value":"summon",    "label":"Yaratık Çağırma (/summon)"},
    {"value":"effect",    "label":"Efekt Verme (/effect)"},
    {"value":"enchant",   "label":"Büyüleme (/enchant)"},
    {"value":"tp",        "label":"Işınlama (/tp)"},
    {"value":"setblock",  "label":"Blok Koy (/setblock)"},
    {"value":"fill",      "label":"Alan Doldur (/fill)"},
    {"value":"execute",   "label":"Koşullu Komut (/execute)"},
    {"value":"scoreboard","label":"Skor Tablosu (/scoreboard)"},
    {"value":"title",     "label":"Ekran Yazısı (/title)"},
    {"value":"tellraw",   "label":"Süslü Sohbet (/tellraw)"},
    {"value":"playsound", "label":"Ses Oynat (/playsound)"},
    {"value":"particle",  "label":"Partikül (/particle)"},
    {"value":"gamerule",  "label":"Oyun Kuralı (/gamerule)"},
    {"value":"data",      "label":"Veri Düzenle (/data)"},
    {"value":"bossbar",   "label":"Boss Bar (/bossbar)"},
    {"value":"attribute", "label":"Özellik (/attribute)"},
    {"value":"tag",       "label":"Etiket (/tag)"},
    {"value":"team",      "label":"Takım (/team)"},
    {"value":"advancement","label":"Başarım (/advancement)"},
    {"value":"schedule",  "label":"Zamanlayıcı (/schedule)"},
    {"value":"loot",      "label":"Loot (/loot)"},
    {"value":"item",      "label":"Eşya Yönet (/item)"},
    {"value":"complex",   "label":"Karmaşık Sistem"},
]

# ── Sürüm Detay Bilgileri ────────────────────────────────────
VERSION_DETAILS = {
    "1.8":    {"era":"LEGACY","label":"1.8 - Klasik (Old School)","desc":"Eski NBT syntax - damage değerleri - Enchant ID'leri sayısal"},
    "1.8.9":  {"era":"LEGACY","label":"1.8.9 - Klasik Stabil","desc":"Eski NBT syntax - /give item:damage formatı - En popüler eski versiyon"},
    "1.9":    {"era":"LEGACY","label":"1.9 - Combat Update","desc":"İkili kılıç sistemi - Yeni ok mekaniği - Eski NBT devam ediyor"},
    "1.9.4":  {"era":"LEGACY","label":"1.9.4 - Combat Stabil","desc":"1.9 döneminin en stabil sürümü - PvP meta değişti"},
    "1.10":   {"era":"LEGACY","label":"1.10 - Frostburn","desc":"Polar ayı ve magma küp eklendi - Eski syntax devam"},
    "1.10.2": {"era":"LEGACY","label":"1.10.2 - Frostburn Stabil","desc":"Bugfix odaklı sürüm - Eski NBT formatı geçerli"},
    "1.11":   {"era":"LEGACY","label":"1.11 - Exploration","desc":"Haberci, Çoban, Evoker - Observer bloğu - Eski syntax"},
    "1.11.2": {"era":"LEGACY","label":"1.11.2 - Exploration Stabil","desc":"Stabil versiyon - Eski komut sistemi son hali"},
    "1.12":   {"era":"LEGACY","label":"1.12 - World of Color","desc":"Renk sistemi yenilendi - Somut bloklar - Eski syntax son"},
    "1.12.2": {"era":"LEGACY","label":"1.12.2 - Son Eski Sürüm","desc":"Eski syntax son versiyonu - NBT ID tabanlı - /give item 1 0 {nbt} formatı"},
    "1.13":   {"era":"MODERN","label":"1.13 - Aquatic (Flattening)","desc":"DEV REBIRTH! Tüm komutlar değişti - Namespace zorunlu - minecraft:item formatı"},
    "1.13.2": {"era":"MODERN","label":"1.13.2 - Aquatic Stabil","desc":"Yeni syntax ilk stabil - /effect give format - execute yenilendi tamamen"},
    "1.14":   {"era":"MODERN","label":"1.14 - Village and Pillage","desc":"Köy yenilendi - /schedule eklendi - tags sistemi - Datapack desteği güçlendi"},
    "1.14.4": {"era":"MODERN","label":"1.14.4 - V&P Stabil","desc":"Önemli bug düzeltmeleri - Modern syntax stabil hale geldi"},
    "1.15":   {"era":"MODERN","label":"1.15 - Buzzy Bees","desc":"Arılar ve bal - Performance iyileştirmeleri - Komut sistemi Modern"},
    "1.15.2": {"era":"MODERN","label":"1.15.2 - Buzzy Bees Stabil","desc":"Bug düzeltmeleri - Modern syntax tam yerleşti"},
    "1.16":   {"era":"MODERN","label":"1.16 - Nether Update","desc":"Nether tamamen yenilendi - Piglins - Yeni biyomlar - Modern komutlar"},
    "1.16.1": {"era":"MODERN","label":"1.16.1","desc":"Nether Update ilk - Soul Speed büyüsü eklendi"},
    "1.16.2": {"era":"MODERN","label":"1.16.2","desc":"Piglin Brute eklendi - Basalt delta biyomu"},
    "1.16.3": {"era":"MODERN","label":"1.16.3","desc":"Stabil Nether - Önemli crash düzeltmeleri"},
    "1.16.4": {"era":"MODERN","label":"1.16.4","desc":"Social Interactions - Çevrimiçi oyuncu engelleme"},
    "1.16.5": {"era":"MODERN","label":"1.16.5 - Nether Stabil","desc":"En popüler 1.16 sürümü - Çoğu mod/server bunu kullanır"},
    "1.17":   {"era":"MODERN","label":"1.17 - Caves and Cliffs Pt.1","desc":"Goat, Axolotl, Glow Squid - 1. parti"},
    "1.17.1": {"era":"MODERN","label":"1.17.1 - C&C Stabil","desc":"Önemli düzeltmeler - Modern syntax devam"},
    "1.18":   {"era":"MODERN","label":"1.18 - Caves and Cliffs Pt.2","desc":"Dev yeraltı mağaraları - Yeni ore dağılımı - attribute güncellemeleri"},
    "1.18.1": {"era":"MODERN","label":"1.18.1","desc":"Cave güncelleme - Spawning düzeltmeleri"},
    "1.18.2": {"era":"MODERN","label":"1.18.2 - Cave Stabil","desc":"Stabil mağara - /locate POI desteği - En iyi 1.18"},
    "1.19":   {"era":"MODERN","label":"1.19 - Wild Update","desc":"Mangrove, Deep Dark, Allay - /summon allay - Warden boss"},
    "1.19.1": {"era":"MODERN","label":"1.19.1","desc":"Chat raporlama sistemi - Küçük düzeltmeler"},
    "1.19.2": {"era":"MODERN","label":"1.19.2","desc":"Kritik güvenlik yaması - Modern syntax devam"},
    "1.19.3": {"era":"MODERN","label":"1.19.3","desc":"Camel ve Sniffer hazırlığı - Inventory değişiklikleri"},
    "1.19.4": {"era":"MODERN","label":"1.19.4 - Wild Stabil","desc":"En popüler 1.19 - /execute store result güçlendi"},
    "1.20":   {"era":"MODERN","label":"1.20 - Trails and Tales","desc":"Bamboo, Camel, Cherry Grove - Archaeology - Modern syntax"},
    "1.20.1": {"era":"MODERN","label":"1.20.1","desc":"Önemli düzeltmeler - En popüler 1.20 başlangıcı"},
    "1.20.2": {"era":"MODERN","label":"1.20.2","desc":"Protocol değişikliği - Birden fazla oyuncu selector"},
    "1.20.4": {"era":"MODERN","label":"1.20.4","desc":"Son eski item syntax - NBT tabanlı son versiyon"},
    "1.20.5": {"era":"COMPONENTS","label":"1.20.5 - COMPONENTS BAŞLADI!","desc":"BÜYÜK DEĞİŞİKLİK! NBT -> Components - /give @p item[component=...] formatı"},
    "1.20.6": {"era":"COMPONENTS","label":"1.20.6 - Components Stabil","desc":"Components format oturdu - Eski NBT çalışmaz!"},
    "1.21":   {"era":"COMPONENTS","label":"1.21 - Tricky Trials","desc":"Trial Chambers - Mace silahı - Breeze mob - Components syntax zorunlu"},
    "1.21.1": {"era":"COMPONENTS","label":"1.21.1","desc":"Trial fix - Components devam - Wind Charge silahı"},
    "1.21.4": {"era":"COMPONENTS","label":"1.21.4 - Bundles of Bravery","desc":"Bundle eklendi - Yeni item özellikleri - Components genişledi"},
    "1.21.5": {"era":"COMPONENTS","label":"1.21.5","desc":"Performance - Components tam stabil - Yeni potion efektleri"},
    "1.21.8": {"era":"COMPONENTS","label":"1.21.8","desc":"Güncel stabil - Components tam oturdu - Tüm yeni itemlar"},
    "1.21.11":{"era":"COMPONENTS","label":"1.21.11 - En Güncel","desc":"En son sürüm - Components syntax tam - pack_format güncel"},
}

def get_era(version):
    v = version.split(".")
    minor = int(v[1]) if len(v) > 1 and v[1].isdigit() else 8
    patch = int(v[2]) if len(v) > 2 and v[2].isdigit() else 0
    if minor < 13: return "LEGACY"
    if minor < 20 or (minor == 20 and patch < 5): return "MODERN"
    return "COMPONENTS"

# ── Sistem Promptları ────────────────────────────────────────

SYSTEM_LEGACY = """You are an elite Minecraft command expert specializing in LEGACY versions (1.8-1.12.2).

CRITICAL RULES:
- ALL commands MUST be in English. NEVER translate command syntax to Turkish.
- Descriptions/explanations are in Turkish, commands always in English.
- Output ONLY valid JSON, no markdown, no extra text.

LEGACY SYNTAX REFERENCE:
/give: /give <player> <item_id>[:<damage>] [count] [nbt]
  - /give @p minecraft:diamond_sword 1 0 {ench:[{id:16,lvl:5},{id:20,lvl:2},{id:35,lvl:3}],display:{Name:"Kutsal Kılıç",Lore:["Efsane silah"]}}
  - Enchant IDs: Prot=0,FireProt=1,FeatherFall=4,Thorns=7,Resp=6,DepthStrider=8,Sharpness=16,Smite=17,BaneArthropod=18,Knockback=19,FireAspect=20,Looting=21,Efficiency=32,SilkTouch=33,Unbreaking=35,Fortune=35,Power=48,Punch=49,Flame=50,Infinity=51,Mending=70,FrostWalker=9
  - Potions: /give @p minecraft:potion 1 0 {Potion:"minecraft:strength",CustomPotionEffects:[{Id:5,Amplifier:4,Duration:200000}]}

/effect: /effect <player> <effectID_number> [seconds] [amplifier]
  - Speed=1,SlowFall=2,Haste=3,MiningFatigue=4,Strength=5,InstantHealth=6,InstantDmg=7,JumpBoost=8,Nausea=9,Regen=10,Resistance=11,FireRes=12,WaterBreathing=13,Invisibility=14,Blindness=15,NightVision=16,Hunger=17,Weakness=18,Poison=19,Wither=20,HealthBoost=21,Absorption=22,Saturation=23
  - /effect @a 1 30 2 (true for hide particles in 1.9+)

/summon: /summon <EntityType> [x y z] [nbt]
  - CamelCase entity names: Zombie, Skeleton, Creeper, Spider, Witch, Wither, EnderDragon
  - /summon Zombie ~ ~1 ~ {CustomName:"Boss",Health:200f,Attributes:[{Name:"generic.maxHealth",Base:200},{Name:"generic.movementSpeed",Base:0.4},{Name:"generic.attackDamage",Base:15},{Name:"generic.armor",Base:10}],Equipment:[{id:268,Count:1},{},{},{},{id:314,Count:1}],PersistenceRequired:1}
  - ArmorStand: /summon ArmorStand ~ ~1 ~ {Invisible:1b,NoGravity:1b,Small:0b,Pose:{Head:[0f,0f,0f]}}

/execute (legacy): /execute <entity> <x> <y> <z> <command>
  - /execute @a ~ ~ ~ detect ~ ~-1 ~ minecraft:grass_block 0 effect @p 1 5 2

/scoreboard:
  - /scoreboard objectives add kills totalKillCount "Kill Sayacı"
  - /scoreboard objectives setdisplay sidebar kills
  - /scoreboard players set @p kills 0
  - /scoreboard players add @p kills 1

/testfor: /testfor @a[r=10,m=0]
/title: /title @a title {"text":"HAZIR","color":"red","bold":true}
/tellraw: /tellraw @a ["",{"text":"Oyuncu: ","color":"yellow"},{"selector":"@p"},{"text":" öldü!","color":"red"}]

Selectors: @a=all,@p=nearest,@r=random,@e=all entities,@s=self(1.9+)
  - @a[r=10,m=0,c=1,team=red,score_kills_min=5,score_kills=10]

COMMAND BLOCK TYPES (where_to_run field):
  "SOHBET / KONSOL" - /slash komutlar, sohbete yazılır
  "IMPULSE KOMUT BLOĞU" - Bir kez çalıştır (el tetiklemeli)
  "REPEATING KOMUT BLOĞU (Always Active)" - Her tick çalışır
  "ZİNCİR KOMUT BLOĞU (Always Active)" - Diğerinin devamı

OUTPUT: Valid JSON only:
{
  "commands": [
    {
      "command": "/exact english command here",
      "description": "Bu komutun ne yaptığının Türkçe açıklaması - detaylı olsun",
      "notes": "Önemli uyarı veya not (varsa, yoksa boş string)",
      "where_to_run": "SOHBET / KONSOL"
    }
  ],
  "explanation": "Tüm sistemin nasıl çalıştığının Türkçe açıklaması",
  "execution_order": "Hangi komutu önce çalıştırman gerektiği",
  "multiple_commands": false,
  "requires_datapack": false,
  "requires_command_block": false,
  "tips": ["ipucu1", "ipucu2"],
  "common_mistakes": ["yaygın hata 1", "yaygın hata 2"]
}"""

SYSTEM_MODERN = """You are an elite Minecraft command expert specializing in MODERN versions (1.13-1.20.4).

CRITICAL RULES:
- ALL commands MUST be in English. NEVER translate command syntax to Turkish.
- Descriptions/explanations are in Turkish, commands always in English.
- Output ONLY valid JSON, no markdown, no extra text.

MODERN SYNTAX REFERENCE (1.13+ Flattening):
/give: /give <selector> <namespace:item>{nbt} [count]
  - /give @p minecraft:diamond_sword{Enchantments:[{id:"minecraft:sharpness",lvl:5},{id:"minecraft:fire_aspect",lvl:2},{id:"minecraft:looting",lvl:3},{id:"minecraft:unbreaking",lvl:3},{id:"minecraft:mending",lvl:1}]} 1
  - Custom name: /give @p minecraft:netherite_sword{display:{Name:'{"text":"Godkiller","color":"dark_red","bold":true,"italic":false}'},Enchantments:[{id:"minecraft:sharpness",lvl:10}],Unbreakable:1b} 1
  - Lore: /give @p minecraft:stick{display:{Name:'{"text":"Magic Staff","color":"light_purple"}',Lore:['["",{"text":"Ancient power","color":"gray"},{"text":" resides here","color":"dark_purple"}]','["",{"text":"Tier: ","color":"yellow"},{"text":"Legendary","color":"gold","bold":true}]']}} 1
  - Potion: /give @p minecraft:potion{Potion:"minecraft:strong_strength",CustomPotionEffects:[{Id:5,Amplifier:4,Duration:200000,ShowParticles:0b}],display:{Name:'{"text":"Mega Güç İksiri","color":"red"}'}} 1

/effect: /effect give <selector> <minecraft:effect> [seconds] [amplifier] [hideParticles]
  - /effect give @a minecraft:speed 30 2 true
  - /effect clear @a minecraft:speed
  - Effects: speed,slowness,haste,mining_fatigue,strength,instant_health,instant_damage,jump_boost,nausea,regeneration,resistance,fire_resistance,water_breathing,invisibility,blindness,night_vision,hunger,weakness,poison,wither,health_boost,absorption,saturation,glowing,levitation,luck,unluck,slow_falling,conduit_power,dolphins_grace,bad_omen,hero_of_the_village,darkness

/summon: /summon <minecraft:entity> [x y z] {nbt}
  - /summon minecraft:zombie ~ ~1 ~ {Health:200.0f,CustomName:'{"text":"MEGA BOSS","color":"red","bold":true}',CustomNameVisible:1b,PersistenceRequired:1b,Attributes:[{Name:"generic.max_health",Base:200.0},{Name:"generic.movement_speed",Base:0.35},{Name:"generic.attack_damage",Base:12.0},{Name:"generic.armor",Base:15.0},{Name:"generic.follow_range",Base:64.0}],ArmorItems:[{id:"minecraft:diamond_boots",Count:1b},{id:"minecraft:diamond_leggings",Count:1b},{id:"minecraft:diamond_chestplate",Count:1b},{id:"minecraft:diamond_helmet",Count:1b}],HandItems:[{id:"minecraft:diamond_sword",Count:1b,tag:{Enchantments:[{id:"minecraft:sharpness",lvl:5}]}}],Tags:["boss","mega"]}

/execute (new):
  - Conditions: as, at, in, positioned, rotated, anchored, if/unless entity/block/blocks/score/biome/loaded/data
  - /execute as @a at @s if block ~ ~-1 ~ minecraft:grass_block run effect give @s minecraft:speed 5 2 true
  - /execute as @a at @s if entity @s[scores={kills=10..}] run title @s title {"text":"10 Kill!","color":"gold","bold":true}
  - /execute store result score @s health run data get entity @s Health 1
  - /execute as @e[type=minecraft:zombie,tag=boss] at @s run particle minecraft:dragon_breath ~ ~1 ~ 0.5 0.5 0.5 0.1 20
  - /execute if score @s kills matches 1.. unless score @s rewarded matches 1.. run give @s minecraft:diamond 1

/scoreboard:
  - /scoreboard objectives add kills minecraft.killed:minecraft.player "Kill Sayısı"
  - /scoreboard objectives setdisplay sidebar kills
  - /scoreboard players add @a kills 0
  - /scoreboard players set @p kills 0
  - /scoreboard players operation @p total += @p kills

/data:
  - /data get entity @s Health
  - /data modify entity @s CustomName set value '{"text":"Yeni İsim","color":"red"}'
  - /data merge entity @s {Glowing:1b,NoAI:1b,Silent:1b,Invulnerable:1b}
  - /data remove entity @s Passengers[0]

/title: /title @a title {"text":"BAŞLIK","color":"red","bold":true}
       /title @a subtitle {"text":"Alt başlık","color":"yellow"}
       /title @a actionbar {"text":"Alt bar","color":"green"}
       /title @a times 10 60 20

/tellraw: /tellraw @a [{"text":"Merhaba "},{"selector":"@p"},{"text":"! Skoru: "},{"score":{"name":"@p","objective":"kills"}},{"text":" öldürme!","color":"gold"}]

/tp: /tp @p 100 64 -200 | /teleport @a ~ ~5 ~ | /tp @p @e[type=minecraft:pig,limit=1,sort=nearest]

/particle: /particle minecraft:flame ~ ~1 ~ 0.3 0.3 0.3 0.1 50 force @a
/playsound: /playsound minecraft:entity.wither.death master @a ~ ~ ~ 1 1 0
/bossbar: /bossbar add mc:boss {"text":"BOSS HP","color":"red"} | /bossbar set mc:boss max 200 | /bossbar set mc:boss value 180 | /bossbar set mc:boss color red | /bossbar set mc:boss style progress
/fill: /fill ~-5 ~-1 ~-5 ~5 ~5 ~5 minecraft:obsidian outline | /fill ~ ~ ~ ~10 ~ ~10 minecraft:air replace minecraft:water
/attribute: /attribute @p minecraft:generic.max_health base set 40
/tag: /tag @p add vip | @e[tag=boss,tag=!dead]
/team: /team add red "Kırmızı Takım" | /team join red @p | /team modify red color red
/advancement: /advancement grant @a only minecraft:story/iron_tools | /advancement revoke @a everything

COMMAND BLOCK TYPES:
  "SOHBET / KONSOL" - /slash komutlar, sohbete yazılır
  "IMPULSE KOMUT BLOĞU" - Bir kez çalıştır
  "REPEATING KOMUT BLOĞU (Always Active)" - Her tick çalışır
  "ZİNCİR KOMUT BLOĞU (Always Active)" - Diğerinin devamı

OUTPUT: Valid JSON only:
{
  "commands": [
    {
      "command": "/exact english command here",
      "description": "Bu komutun ne yaptığının detaylı Türkçe açıklaması",
      "notes": "Önemli uyarı (varsa)",
      "where_to_run": "SOHBET / KONSOL"
    }
  ],
  "explanation": "Sistemin nasıl çalıştığı - Türkçe",
  "execution_order": "Sıra açıklaması",
  "multiple_commands": false,
  "requires_datapack": false,
  "requires_command_block": false,
  "tips": ["ipucu"],
  "common_mistakes": ["hata"]
}"""

SYSTEM_COMPONENTS = """You are an elite Minecraft command expert specializing in COMPONENTS era (1.20.5+).

CRITICAL RULES:
- ALL commands MUST be in English. NEVER translate command syntax to Turkish.
- Descriptions/explanations are in Turkish, commands always in English.
- NEVER use old NBT curly braces for items! Use [component=value] format!
- Output ONLY valid JSON, no markdown, no extra text.

COMPONENTS SYNTAX REFERENCE (1.20.5+):
/give: /give <selector> <namespace:item>[components] [count]
  ENCHANTMENTS:
  - /give @p minecraft:netherite_sword[minecraft:enchantments={levels:{"minecraft:sharpness":10,"minecraft:fire_aspect":5,"minecraft:looting":10,"minecraft:mending":1,"minecraft:unbreaking":10,"minecraft:sweeping_edge":3}}] 1

  CUSTOM NAME:
  - /give @p minecraft:netherite_sword[minecraft:custom_name='{"text":"Godslayer","color":"dark_red","bold":true,"italic":false}'] 1

  LORE:
  - /give @p minecraft:diamond[minecraft:lore=['{"text":"Efsanevi Parça","color":"light_purple","italic":false}','{"text":"Void'dan dövülmüş","color":"dark_purple","italic":true}']] 1

  UNBREAKABLE:
  - /give @p minecraft:diamond_sword[minecraft:unbreakable={}] 1
  - /give @p minecraft:diamond_sword[minecraft:unbreakable={show_in_tooltip:false}] 1

  ENCHANT GLINT:
  - /give @p minecraft:nether_star[minecraft:enchantment_glint_override=true] 1

  CUSTOM DATA:
  - /give @p minecraft:nether_star[minecraft:custom_data={boss_egg:1,tier:3,owner:"player1"}] 1

  ATTRIBUTE MODIFIERS:
  - /give @p minecraft:diamond_chestplate[minecraft:attribute_modifiers={modifiers:[{type:"minecraft:generic.armor",amount:20.0,operation:"add_value",slot:"chest",id:"bonus_armor"}]}] 1

  HIDE TOOLTIP:
  - /give @p minecraft:paper[minecraft:hide_tooltip={}] 1

  FOOD:
  - /give @p minecraft:stick[minecraft:food={nutrition:20,saturation_modifier:20.0,can_always_eat:true}] 1

  FULL OP SWORD EXAMPLE:
  - /give @p minecraft:netherite_sword[minecraft:enchantments={levels:{"minecraft:sharpness":10,"minecraft:fire_aspect":5,"minecraft:looting":10,"minecraft:mending":1,"minecraft:unbreaking":10}},minecraft:custom_name='{"text":"Godslayer","color":"dark_red","bold":true,"italic":false}',minecraft:lore=['{"text":"Void ile dövülmüş","color":"dark_purple","italic":true}'],minecraft:unbreakable={show_in_tooltip:false},minecraft:enchantment_glint_override=true] 1

/effect: Same as modern but new 1.21 effects:
  - /effect give @a minecraft:wind_charged 60 1 true
  - /effect give @a minecraft:weaving 60 1 true
  - /effect give @a minecraft:oozing 60 1 true
  - /effect give @a minecraft:infested 60 1 true

/summon: Still uses NBT for entities:
  - /summon minecraft:zombie ~ ~1 ~ {Health:200.0f,Attributes:[{"Name":"minecraft:generic.max_health","Base":200.0},{"Name":"minecraft:generic.scale","Base":3.0},{"Name":"minecraft:generic.attack_damage","Base":20.0},{"Name":"minecraft:generic.armor","Base":20.0},{"Name":"minecraft:generic.movement_speed","Base":0.4},{"Name":"minecraft:generic.follow_range","Base":64.0}],CustomName:'{"text":"TITAN BOSS","color":"dark_red","bold":true}',CustomNameVisible:1b,PersistenceRequired:1b,NoAI:0b,Tags:["boss","titan"],HandItems:[{id:"minecraft:netherite_sword",count:1,components:{"minecraft:enchantments":{levels:{"minecraft:sharpness":10}}}},{}}]}

/execute: Same as modern plus:
  - /execute store result score #random rand run random value 0..99
  - /execute as @a at @s if block ~ ~-1 ~ minecraft:grass_block run effect give @s minecraft:speed 5 2 true

/item: /item replace entity @p armor.head with minecraft:diamond_helmet[minecraft:enchantments={levels:{"minecraft:protection":10,"minecraft:respiration":3,"minecraft:aqua_affinity":1}}]
       /item replace block ~ ~1 ~ container.0 with minecraft:diamond 64

/attribute: /attribute @p minecraft:generic.max_health base set 100
            /attribute @p minecraft:generic.movement_speed base set 0.2
            /attribute @p minecraft:player.block_break_speed base set 5.0

/schedule: /schedule function namespace:my_function 100t append
           /schedule function namespace:loop 20t replace

BOSSBAR, SCOREBOARD, TITLE, TELLRAW, TAG, TEAM: Same as MODERN era.

COMMAND BLOCK TYPES:
  "SOHBET / KONSOL" - /slash komutlar, sohbete yazılır
  "IMPULSE KOMUT BLOĞU" - Bir kez çalıştır
  "REPEATING KOMUT BLOĞU (Always Active)" - Her tick çalışır
  "ZİNCİR KOMUT BLOĞU (Always Active)" - Diğerinin devamı

OUTPUT: Valid JSON only:
{
  "commands": [
    {
      "command": "/exact english command here",
      "description": "Bu komutun ne yaptığının detaylı Türkçe açıklaması",
      "notes": "Components syntax hakkında önemli not (varsa)",
      "where_to_run": "SOHBET / KONSOL"
    }
  ],
  "explanation": "Sistemin nasıl çalıştığı - Türkçe",
  "execution_order": "Sıra açıklaması",
  "multiple_commands": false,
  "requires_datapack": false,
  "requires_command_block": false,
  "tips": ["ipucu"],
  "common_mistakes": ["components ile ilgili yaygın hata"]
}"""

def get_system(version):
    era = get_era(version)
    prompts = {"LEGACY": SYSTEM_LEGACY, "MODERN": SYSTEM_MODERN, "COMPONENTS": SYSTEM_COMPONENTS}
    base = prompts[era]
    detail = VERSION_DETAILS.get(version, {})
    extra = "\n\nGenerating for Minecraft Java Edition " + version + ". "
    extra += detail.get("desc", "") + " "
    extra += "All commands MUST be 100% correct for version " + version + " specifically. "
    extra += "Return ONLY valid JSON."
    return base + extra

# ── API Cagri Fonksiyonu ─────────────────────────────────────

def call_api(msgs):
    """Cerebras API'ye istek gonder. Retry ve hata yonetimi icerir."""
    if not API_KEY:
        raise ValueError("CEREBRAS_API_KEY ortam degiskeni ayarlanmamis!")

    session = get_retry_session()
    start_time = time.time()

    try:
        resp = session.post(
            API_URL,
            headers={
                "Authorization": "Bearer " + API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "model": MODEL,
                "messages": msgs,
                "max_tokens": MAX_TOKENS,
                "stream": False
            },
            timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()

        elapsed = time.time() - start_time
        logger.info("API yaniti alindi: %.2fs", elapsed)

        data = resp.json()
        return data["choices"][0]["message"]["content"]

    except requests.exceptions.Timeout:
        logger.error("API zaman asimina ugradi")
        raise TimeoutError("API zaman asimina ugradi. Lutfen tekrar deneyin.")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else "Bilinmiyor"
        logger.error("API HTTP hatasi: %s - %s", status, str(e))
        if status == 401:
            raise ValueError("API anahtari gecersiz. Lutfen CEREBRAS_API_KEY'i kontrol edin.")
        elif status == 429:
            raise ValueError("Rate limit asildi. Lutfen birkac saniye bekleyin.")
        elif status >= 500:
            raise ValueError("Cerebras sunucu hatasi (%s). Lutfen daha sonra tekrar deneyin." % status)
        else:
            raise ValueError("API hatasi: %s" % str(e)[:200])
    except requests.exceptions.ConnectionError:
        logger.error("API baglanti hatasi")
        raise ConnectionError("Cerebras API'ye baglanilamiyor. Internet baglantinizi kontrol edin.")
    except Exception as e:
        logger.error("Beklenmeyen API hatasi: %s", str(e))
        raise RuntimeError("Beklenmeyen hata: %s" % str(e)[:200])

# ── JSON Parse Fonksiyonu (Robust) ───────────────────────────

def parse_response(raw):
    """AI yanitini guvenli sekilde JSON'a cevir."""
    raw = raw.strip()

    # Markdown code block temizle
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s*```\s*$', '', raw)
    raw = raw.strip()

    # Bos yanit kontrolu
    if not raw:
        raise ValueError("API bos yanit dondurdu")

    # Dogrudan parse dene
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # String icerisindeki newline karakterlerini escape et
    def fix_json_string(text):
        result = []
        in_string = False
        escape_next = False
        i = 0
        while i < len(text):
            c = text[i]
            if escape_next:
                result.append(c)
                escape_next = False
                i += 1
                continue
            if c == "\\":
                result.append(c)
                escape_next = True
                i += 1
                continue
            if c == '"' and not escape_next:
                in_string = not in_string
            elif in_string and c == "\n":
                result.append("\\n")
                i += 1
                continue
            elif in_string and c == "\r":
                i += 1
                continue
            elif in_string and c == "\t":
                result.append("\\t")
                i += 1
                continue
            result.append(c)
            i += 1
        return "".join(result)

    try:
        fixed = fix_json_string(raw)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # JSON objesi ara (en son care)
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        try:
            fixed = fix_json_string(m.group(0))
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # Hata detayini logla
    logger.error("JSON parse basarisiz. Ham veri (ilk 500 karakter): %s", raw[:500])
    raise ValueError("AI yaniti JSON formatinda degil. Ham veri: %s..." % raw[:200])

# ── Yanit Dogrulama ──────────────────────────────────────────

def validate_response(parsed, version):
    """AI yanitinin yapisini dogrula."""
    if not isinstance(parsed, dict):
        raise ValueError("AI yaniti JSON obje degil")

    commands = parsed.get("commands", [])
    if not commands:
        raise ValueError("AI yanitinda komut bulunamadi")

    for i, cmd in enumerate(commands):
        if not isinstance(cmd, dict):
            raise ValueError("Komut %d obje degil" % (i+1))
        if "command" not in cmd:
            raise ValueError("Komut %d 'command' alani icermiyor" % (i+1))
        if not cmd["command"] or not cmd["command"].startswith("/"):
            raise ValueError("Komut %d gecersiz: %s" % (i+1, cmd.get("command", "BOS")))

    # Varsayilan degerleri ekle
    defaults = {
        "explanation": "",
        "execution_order": "",
        "multiple_commands": False,
        "requires_datapack": False,
        "requires_command_block": False,
        "tips": [],
        "common_mistakes": []
    }

    for key, val in defaults.items():
        if key not in parsed:
            parsed[key] = val

    return parsed

# ── Flask Route'ları ─────────────────────────────────────────

@app.route("/")
def index():
    """Ana sayfa - index.html render et."""
    return render_template("index.html",
        versions=VERSIONS,
        command_types=COMMAND_TYPES,
        version_details=VERSION_DETAILS
    )

@app.route("/ping")
def ping():
    """Sağlık kontrolü endpoint'i."""
    return jsonify({
        "ok": True,
        "model": MODEL,
        "api": "Cerebras",
        "api_key_configured": bool(API_KEY),
        "timestamp": time.time()
    })

@app.route("/version-info/<version>")
def version_info(version):
    """Sürüm detay bilgilerini döndür."""
    detail = VERSION_DETAILS.get(version, {})
    era = get_era(version)
    return jsonify({
        "era": era,
        "version": version,
        **detail
    })

@app.route('/generate', methods=['POST'])
def generate():
    """Ana komut üretim endpoint'i."""
    start_time = time.time()

    # API key kontrolü
    if not API_KEY:
        logger.error("API anahtarı ayarlanmamış")
        return jsonify({
            "success": False,
            "error": "API anahtarı ayarlanmamış. Lütfen CEREBRAS_API_KEY ortam değişkenini ayarlayın."
        }), 503

    # İstek verisini al
    try:
        data = request.get_json(force=True)
    except Exception as e:
        logger.warning(f"Geçersiz JSON isteği: {str(e)}")
        return jsonify({
            "success": False,
            "error": "Geçersiz JSON formatı. Lütfen isteğinizi kontrol edin."
        }), 400

    idea = data.get("idea", "").strip()
    version = data.get("version", "1.21.11")
    platform = data.get("platform", "Java")
    cmd_type = data.get("command_type", "all")

    # Validasyon
    if not idea:
        return jsonify({
            "success": False,
            "error": "Bir fikir yazmalısın!"
        }), 400

    if version not in VERSIONS:
        return jsonify({
            "success": False,
            "error": f"Geçersiz sürüm: {version}. Desteklenen sürümler: {', '.join(VERSIONS[:5])}..."
        }), 400

    era = get_era(version)
    type_hint = f" Komut türü: {cmd_type}." if cmd_type != "all" else ""

    user_msg = (
        f"Minecraft Java Edition {version} için şu isteği komutlara dönüştür: {idea}.{type_hint} "
        f"Sürüm erası: {era}. Tüm komutlar bu sürüm için %100 doğru ve çalışır olmalı."
    )

    logger.info(f"Komut üretim isteği: sürüm={version}, era={era}, idea={idea[:50]}...")

    # API çağrısı
    try:
        raw = call_api([
            {"role": "system", "content": get_system(version)},
            {"role": "user",   "content": user_msg}
        ])
    except TimeoutError as e:
        logger.error(f"Zaman aşımı: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "retry_after": 5
        }), 504
    except (ValueError, ConnectionError, RuntimeError) as e:
        logger.error(f"API hatası: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
    except Exception as e:
        logger.error(f"Beklenmeyen hata: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"Beklenmeyen hata: {str(e)[:200]}"
        }), 500

    # JSON parse
    try:
        parsed = parse_response(raw)
        validated = validate_response(parsed, version)
    except ValueError as e:
        logger.error(f"Parse hatası: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"AI yanıtı işlenemedi: {str(e)[:200]}",
            "raw_preview": raw[:300] if 'raw' in dir() else "Yok"
        }), 500
    except Exception as e:
        logger.error(f"Doğrulama hatası: {str(e)}")
        return jsonify({
            "success": False,
            "error": f"Yanıt doğrulanamadı: {str(e)[:200]}"
        }), 500

    # Başarılı yanıt
    detail = VERSION_DETAILS.get(version, {})
    elapsed = time.time() - start_time

    logger.info(f"Komut üretimi başarılı: {len(validated.get('commands', []))} komut, {elapsed:.2f}s")

    response_data = {
        "success": True,
        "version": version,
        "era": era,
        "era_label": detail.get("label", version),
        "platform": platform,
        "command_type": cmd_type,
        "commands": validated.get("commands", []),
        "explanation": validated.get("explanation", ""),
        "execution_order": validated.get("execution_order", ""),
        "multiple_commands": validated.get("multiple_commands", False),
        "requires_datapack": validated.get("requires_datapack", False),
        "requires_command_block": validated.get("requires_command_block", False),
        "tips": validated.get("tips", []),
        "common_mistakes": validated.get("common_mistakes", []),
        "generation_time": round(elapsed, 2)
    }

    return jsonify(response_data)

# ── Hata Yakalayıcılar ───────────────────────────────────────

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Endpoint bulunamadı. Kullanılabilir endpoint'ler: /, /ping, /generate, /version-info/<version>"
    }), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({
        "success": False,
        "error": "Bu endpoint bu HTTP metodunu desteklemiyor."
    }), 405

@app.errorhandler(500)
def internal_error(error):
    import traceback
    logger.error(f"Sunucu hatası: {str(error)}")
    logger.error(traceback.format_exc())
    return jsonify({
        "success": False,
        "error": "Sunucu hatası oluştu. Detay: " + str(error)[:200]
    }), 500

# ── Static Dosyalar (Sadece Gerekli Olanlar) ─────────────────

@app.route('/icon.png')
def icon():
    return send_from_directory('.', 'icon.png')

@app.route('/kanal.png')
def kanal():
    return send_from_directory('.', 'kanal.png')

@app.route('/manifest.json')
def manifest():
    return send_from_directory('.', 'manifest.json')

@app.route('/sw.js')
def sw():
    return send_from_directory('.', 'sw.js')

# ── Ana Çalıştırma ───────────────────────────────────────────


# ── MongoDB Bağlantısı ───────────────────────────────────────
try:
    from pymongo import MongoClient, ASCENDING, DESCENDING
    from pymongo.errors import PyMongoError
    from bson.objectid import ObjectId
    from bson.errors import InvalidId
    MONGODB_URI = os.environ.get("MONGODB_URI")
    if MONGODB_URI:
        mongo_client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            socketTimeoutMS=10000,
            maxPoolSize=10,
            retryWrites=True
        )
        mongo_client.admin.command('ping')
        db = mongo_client["mc_komut"]
        community_collection = db["community_posts"]
        comments_collection = db["community_comments"]
        users_collection = db["community_users"]
        # Index oluştur (performans için)
        community_collection.create_index("approved")
        community_collection.create_index("date")
        community_collection.create_index("version")
        community_collection.create_index([("likes", DESCENDING)])
        comments_collection.create_index("post_id")
        comments_collection.create_index("date")
        users_collection.create_index("username", unique=True)
        users_collection.create_index("token")
        logger.info("MongoDB bağlantısı başarılı")
    else:
        logger.warning("MONGODB_URI ayarlanmamış - Topluluk özelliği devre dışı")
        mongo_client = None
        db = None
        community_collection = None
        comments_collection = None
        users_collection = None
except Exception as e:
    logger.error("MongoDB bağlantı hatası: %s", str(e))
    mongo_client = None
    db = None
    community_collection = None
    comments_collection = None
    users_collection = None

# ── Küfür ve Spam Filtresi ──────────────────────────────────
BAD_WORDS = [
    "amk", "aq", "oç", "orospu", "piç", "sik", "siktir", "yarrak", "göt", "meme", "orosbu",
    "pezevenk", "kahpe", "dalyarak", "amcık", "gavat", "ibne", "kevaşe", "sürtük", "fahişe",
    "yavşak", "mal", "aptal", "gerizekalı", "salak", "moron", "idiot", "retard", "fuck",
    "shit", "bitch", "ass", "damn", "crap", "hell", "bastard", "dick", "cock", "pussy",
    "whore", "slut", "nigger", "nigga", "fag", "cunt", "wanker", "twat",
    "orospunun", "piçin", "amın", "sikik", "götveren", "pezevengin", "kahpenin",
    "ananı", "babanı", "sikerim", "siktim", "sikiyim", "siktirgit", "siktr",
    "amcığ", "götün", "yarak", "orospu çocuğu", "piç kurusu", "amk oç",
    "gerzek", "aptal sürüsü", "mal herif", "salak herif", "gerizekalı herif"
]

def check_content(text, strict_mode=False, min_length=10):
    """İçerik kontrolü - küfür ve spam filtreleme.

    strict_mode=True: Tam kelime sınırı eşleşmesi (kullanıcı adları için)
    strict_mode=False: Daha esnek kontrol (içerik için)
    min_length: Minimum karakter sayısı (içerik için 10, kullanıcı adı için 3)
    """
    import re
    text_lower = text.lower()

    for word in BAD_WORDS:
        word_lower = word.lower()
        if strict_mode:
            # Tam kelime eşleşmesi - kelime sınırları arasında ara
            # Türkçe karakterler ve alfanümerik karakterleri destekle
            pattern = r'(?:^|[^a-zA-Z0-9ğüşıöçĞÜŞİÖÇ])' + re.escape(word_lower) + r'(?:[^a-zA-Z0-9ğüşıöçĞÜŞİÖÇ]|$)'
            if re.search(pattern, text_lower):
                return False, "İçerik uygunsuz kelime içeriyor"
        else:
            # İçerik için substring kontrolü (kısa kelimeler hariç)
            if len(word_lower) <= 3:
                # Kısa kelimeler için tam kelime eşleşmesi
                pattern = r'(?:^|[^a-zA-Z0-9ğüşıöçĞÜŞİÖÇ])' + re.escape(word_lower) + r'(?:[^a-zA-Z0-9ğüşıöçĞÜŞİÖÇ]|$)'
                if re.search(pattern, text_lower):
                    return False, "İçerik uygunsuz kelime içeriyor"
            else:
                # Uzun kelimeler için substring kontrolü
                if word_lower in text_lower:
                    return False, "İçerik uygunsuz kelime içeriyor"

    if len(text) < min_length:
        return False, f"İçerik çok kısa (en az {min_length} karakter)"
    return True, ""

def hash_ip(ip):
    """IP hashleme - rate limit için"""
    return hashlib.sha256(ip.encode()).hexdigest()[:16]

# ── Topluluk Endpointleri ────────────────────────────────────

@app.route("/community")
def community_page():
    """Topluluk sayfasını render et."""
    return render_template("topluluk.html",
        versions=VERSIONS,
        command_types=COMMAND_TYPES
    )

@app.route("/community/post/<post_id>")
def post_detail_page(post_id):
    """Gönderi detay sayfasını render et."""
    return render_template("post.html",
        post_id=post_id,
        versions=VERSIONS
    )

@app.route("/api/community/posts", methods=["GET"])
def get_community_posts():
    """Onaylanmış paylaşımları getir."""
    if community_collection is None:
        return jsonify({
            "success": False,
            "error": "Topluluk özelliği şu an kullanılamıyor."
        }), 503

    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))
        version_filter = request.args.get("version", "")
        sort_by = request.args.get("sort", "newest")

        skip = (page - 1) * per_page

        query = {"approved": True}
        if version_filter:
            query["version"] = version_filter

        if sort_by == "popular":
            sort_field = [("likes", -1), ("date", -1)]
        else:
            sort_field = [("date", -1)]

        posts = list(community_collection.find(
            query,
            {"ip_hash": 0}
        ).sort(sort_field).skip(skip).limit(per_page))

        total = community_collection.count_documents(query)

        for post in posts:
            post["_id"] = str(post["_id"])
            post["date"] = post["date"].isoformat() if "date" in post else ""

        return jsonify({
            "success": True,
            "posts": posts,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page
        })

    except Exception as e:
        logger.error("Topluluk listeleme hatası: %s", str(e))
        return jsonify({
            "success": False,
            "error": "Paylaşımlar getirilemedi."
        }), 500

@app.route("/api/community/post", methods=["POST"])
def create_community_post():
    """Yeni paylaşım oluştur."""
    if community_collection is None:
        return jsonify({
            "success": False,
            "error": "Topluluk özelliği şu an kullanılamıyor."
        }), 503

    try:
        data = request.get_json(force=True)
        title = data.get("title", "").strip()
        content = data.get("content", "").strip()
        author = data.get("author", "Anonim").strip()[:20]
        version = data.get("version", "1.21.11")

        if not title or not content:
            return jsonify({
                "success": False,
                "error": "Başlık ve içerik zorunlu."
            }), 400

        ok, msg = check_content(title + " " + content)
        if not ok:
            return jsonify({
                "success": False,
                "error": msg
            }), 400

        ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
        ip_hash = hash_ip(ip)
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        today_count = community_collection.count_documents({
            "ip_hash": ip_hash,
            "date": {"$gte": today}
        })

        if today_count >= 5:
            return jsonify({
                "success": False,
                "error": "Günlük paylaşım limitine ulaştınız (max 5/gün)."
            }), 429

        post = {
            "title": title[:200],
            "content": content,
            "author": author if author else "Anonim",
            "version": version,
            "date": datetime.now(),
            "likes": 0,
            "reports": 0,
            "approved": True,
            "ip_hash": ip_hash,
            "liked_by": [],
            "comments_closed": False
        }

        result = community_collection.insert_one(post)

        logger.info("Yeni paylaşım oluşturuldu: %s", title[:50])

        return jsonify({
            "success": True,
            "message": "Paylaşımınız yayınlandı! Teşekkürler.",
            "post_id": str(result.inserted_id)
        })

    except Exception as e:
        logger.error("Paylaşım oluşturma hatası: %s", str(e))
        return jsonify({
            "success": False,
            "error": "Paylaşım oluşturulamadı."
        }), 500

@app.route("/api/community/like/<post_id>", methods=["POST"])
def like_post(post_id):
    """Paylaşımı beğen."""
    if community_collection is None:
        return jsonify({"success": False, "error": "Topluluk devre dışı"}), 503

    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
        ip_hash = hash_ip(ip)

        post = community_collection.find_one({"_id": ObjectId(post_id)})
        if not post:
            return jsonify({"success": False, "error": "Paylaşım bulunamadı."}), 404

        if ip_hash in post.get("liked_by", []):
            community_collection.update_one(
                {"_id": ObjectId(post_id)},
                {"$inc": {"likes": -1}, "$pull": {"liked_by": ip_hash}}
            )
            new_likes = post["likes"] - 1
            return jsonify({"success": True, "likes": max(0, new_likes), "liked": False})

        community_collection.update_one(
            {"_id": ObjectId(post_id)},
            {"$inc": {"likes": 1}, "$push": {"liked_by": ip_hash}}
        )
        return jsonify({"success": True, "likes": post["likes"] + 1, "liked": True})

    except Exception as e:
        logger.error("Beğenme hatası: %s", str(e))
        return jsonify({"success": False, "error": "İşlem başarısız."}), 500

@app.route("/api/community/report/<post_id>", methods=["POST"])
def report_post(post_id):
    """Paylaşımı şikayet et."""
    if community_collection is None:
        return jsonify({"success": False, "error": "Topluluk devre dışı"}), 503

    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
        ip_hash = hash_ip(ip)

        # Aynı IP aynı gönderiyi tekrar şikayet edemez
        post = community_collection.find_one({"_id": ObjectId(post_id)})
        if not post:
            return jsonify({"success": False, "error": "Paylaşım bulunamadı."}), 404

        reported_by = post.get("reported_by", [])
        if ip_hash in reported_by:
            return jsonify({"success": False, "error": "Bu paylaşımı zaten şikayet ettiniz."}), 429

        community_collection.update_one(
            {"_id": ObjectId(post_id)},
            {"$inc": {"reports": 1}, "$push": {"reported_by": ip_hash}}
        )

        post = community_collection.find_one({"_id": ObjectId(post_id)})
        if post and post.get("reports", 0) >= 10:
            community_collection.update_one(
                {"_id": ObjectId(post_id)},
                {"$set": {"approved": False}}
            )

        return jsonify({
            "success": True,
            "message": "Şikayetiniz alındı. Teşekkürler."
        })

    except Exception as e:
        logger.error("Şikayet hatası: %s", str(e))
        return jsonify({"success": False, "error": "İşlem başarısız."}), 500

@app.route("/api/community/post/<post_id>", methods=["GET"])
def get_single_post(post_id):
    """Tek paylaşım detayı."""
    if community_collection is None:
        return jsonify({"success": False, "error": "Topluluk devre dışı"}), 503

    try:
        post = community_collection.find_one(
            {"_id": ObjectId(post_id), "approved": True},
            {"ip_hash": 0, "liked_by": 0}
        )

        if not post:
            return jsonify({"success": False, "error": "Paylaşım bulunamadı."}), 404

        post["_id"] = str(post["_id"])
        post["date"] = post["date"].isoformat() if "date" in post else ""

        # Yorumları getir
        comments = []
        if comments_collection is not None and not post.get("comments_closed", False):
            raw_comments = list(comments_collection.find(
                {"post_id": post_id}
            ).sort("date", -1).limit(100))
            for c in raw_comments:
                c["_id"] = str(c["_id"])
                c["date"] = c["date"].isoformat() if "date" in c else ""
                comments.append(c)

        post["comments"] = comments
        post["comment_count"] = len(comments)

        return jsonify({"success": True, "post": post})

    except Exception as e:
        logger.error("Paylaşım getirme hatası: %s", str(e))
        return jsonify({"success": False, "error": "Paylaşım getirilemedi."}), 500


# ── YORUM ENDPOINTLERİ ──────────────────────────────────────

@app.route("/api/community/post/<post_id>/comments", methods=["GET"])
def get_comments(post_id):
    """Bir gönderinin yorumlarını getir."""
    if comments_collection is None:
        return jsonify({"success": False, "error": "Topluluk devre dışı"}), 503

    try:
        # Önce gönderinin yorumları kapalı mı kontrol et
        post = community_collection.find_one(
            {"_id": ObjectId(post_id)},
            {"comments_closed": 1}
        )
        if post and post.get("comments_closed", False):
            return jsonify({"success": True, "comments": [], "closed": True})

        comments = list(comments_collection.find(
            {"post_id": post_id}
        ).sort("date", -1).limit(100))

        for c in comments:
            c["_id"] = str(c["_id"])
            c["date"] = c["date"].isoformat() if "date" in c else ""

        return jsonify({"success": True, "comments": comments, "closed": False})

    except Exception as e:
        logger.error("Yorum getirme hatası: %s", str(e))
        return jsonify({"success": False, "error": "Yorumlar getirilemedi."}), 500


@app.route("/api/community/post/<post_id>/comment", methods=["POST"])
def add_comment(post_id):
    """Yeni yorum ekle."""
    if comments_collection is None or community_collection is None:
        return jsonify({"success": False, "error": "Topluluk devre dışı"}), 503

    try:
        # Gönderinin yorumları kapalı mı kontrol et
        post = community_collection.find_one(
            {"_id": ObjectId(post_id)},
            {"comments_closed": 1}
        )
        if post and post.get("comments_closed", False):
            return jsonify({"success": False, "error": "Bu gönderinin yorumları kapalı."}), 403

        data = request.get_json(force=True)
        content = data.get("content", "").strip()
        author = data.get("author", "Anonim").strip()[:20]

        if not content:
            return jsonify({"success": False, "error": "Yorum içeriği boş olamaz."}), 400

        if len(content) > 1000:
            return jsonify({"success": False, "error": "Yorum en fazla 1000 karakter olabilir."}), 400

        # Küfür kontrolü
        ok, msg = check_content(content)
        if not ok:
            return jsonify({"success": False, "error": msg}), 400

        # Etiketleri bul (@kullaniciadi)
        mentions = re.findall(r'@([a-zA-Z0-9_ğüşıöçĞÜŞİÖÇ]+)', content)

        comment = {
            "post_id": post_id,
            "content": content,
            "author": author if author else "Anonim",
            "date": datetime.now(),
            "likes": 0,
            "mentions": mentions
        }

        result = comments_collection.insert_one(comment)

        return jsonify({
            "success": True,
            "message": "Yorumunuz eklendi.",
            "comment_id": str(result.inserted_id)
        })

    except Exception as e:
        logger.error("Yorum ekleme hatası: %s", str(e))
        return jsonify({"success": False, "error": "Yorum eklenemedi."}), 500


@app.route("/api/community/post/<post_id>/close-comments", methods=["POST"])
def close_comments(post_id):
    """Gönderi sahibi yorumları kapatır."""
    if community_collection is None:
        return jsonify({"success": False, "error": "Topluluk devre dışı"}), 503

    try:
        community_collection.update_one(
            {"_id": ObjectId(post_id)},
            {"$set": {"comments_closed": True}}
        )
        return jsonify({"success": True, "message": "Yorumlar kapatıldı."})
    except Exception as e:
        logger.error("Yorum kapatma hatası: %s", str(e))
        return jsonify({"success": False, "error": "İşlem başarısız."}), 500


@app.route("/api/community/post/<post_id>/open-comments", methods=["POST"])
def open_comments(post_id):
    """Gönderi sahibi yorumları açar."""
    if community_collection is None:
        return jsonify({"success": False, "error": "Topluluk devre dışı"}), 503

    try:
        community_collection.update_one(
            {"_id": ObjectId(post_id)},
            {"$set": {"comments_closed": False}}
        )
        return jsonify({"success": True, "message": "Yorumlar açıldı."})
    except Exception as e:
        logger.error("Yorum açma hatası: %s", str(e))
        return jsonify({"success": False, "error": "İşlem başarısız."}), 500


# ── KULLANICI SİSTEMİ (Basit Token-based) ───────────────────

def generate_token():
    """Rastgele token üret."""
    return secrets.token_urlsafe(32)


@app.route("/api/auth/register", methods=["POST"])
def register_user():
    """Yeni kullanıcı kaydı."""
    if users_collection is None:
        return jsonify({"success": False, "error": "Kullanıcı sistemi devre dışı"}), 503

    try:
        data = request.get_json(force=True)
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        if not username or not password:
            return jsonify({"success": False, "error": "Kullanıcı adı ve şifre zorunlu."}), 400

        if len(username) < 3 or len(username) > 20:
            return jsonify({"success": False, "error": "Kullanıcı adı 3-20 karakter olmalı."}), 400

        if len(password) < 4:
            return jsonify({"success": False, "error": "Şifre en az 4 karakter olmalı."}), 400

        # Küfür kontrolü (tam kelime eşleşmesi, min 3 karakter)
        ok, msg = check_content(username, strict_mode=True, min_length=3)
        if not ok:
            return jsonify({"success": False, "error": msg}), 400

        # Şifreyi hashle
        password_hash = hashlib.sha256(password.encode()).hexdigest()

        # Kullanıcı var mı kontrol et
        existing = users_collection.find_one({"username": username})
        if existing:
            return jsonify({"success": False, "error": "Bu kullanıcı adı zaten alınmış."}), 409

        token = generate_token()

        user = {
            "username": username,
            "password_hash": password_hash,
            "token": token,
            "created_at": datetime.now(),
            "avatar": "",
            "bio": ""
        }

        users_collection.insert_one(user)

        return jsonify({
            "success": True,
            "message": "Kayıt başarılı!",
            "token": token,
            "username": username
        })

    except Exception as e:
        logger.error("Kayıt hatası: %s", str(e))
        return jsonify({"success": False, "error": "Kayıt başarısız."}), 500


@app.route("/api/auth/login", methods=["POST"])
def login_user():
    """Kullanıcı girişi."""
    if users_collection is None:
        return jsonify({"success": False, "error": "Kullanıcı sistemi devre dışı"}), 503

    try:
        data = request.get_json(force=True)
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        if not username or not password:
            return jsonify({"success": False, "error": "Kullanıcı adı ve şifre zorunlu."}), 400

        password_hash = hashlib.sha256(password.encode()).hexdigest()

        user = users_collection.find_one({
            "username": username,
            "password_hash": password_hash
        })

        if not user:
            return jsonify({"success": False, "error": "Kullanıcı adı veya şifre hatalı."}), 401

        # Yeni token üret
        token = generate_token()
        users_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"token": token}}
        )

        return jsonify({
            "success": True,
            "token": token,
            "username": username,
            "avatar": user.get("avatar", ""),
            "bio": user.get("bio", "")
        })

    except Exception as e:
        logger.error("Giriş hatası: %s", str(e))
        return jsonify({"success": False, "error": "Giriş başarısız."}), 500


@app.route("/api/auth/me", methods=["GET"])
def get_me():
    """Token ile kullanıcı bilgisi getir."""
    if users_collection is None:
        return jsonify({"success": False, "error": "Kullanıcı sistemi devre dışı"}), 503

    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"success": False, "error": "Token gerekli."}), 401

        user = users_collection.find_one({"token": token})
        if not user:
            return jsonify({"success": False, "error": "Geçersiz token."}), 401

        return jsonify({
            "success": True,
            "username": user["username"],
            "avatar": user.get("avatar", ""),
            "bio": user.get("bio", "")
        })

    except Exception as e:
        logger.error("Token kontrol hatası: %s", str(e))
        return jsonify({"success": False, "error": "İşlem başarısız."}), 500


@app.route("/api/auth/update-profile", methods=["POST"])
def update_profile():
    """Profil güncelle (avatar URL, bio)."""
    if users_collection is None:
        return jsonify({"success": False, "error": "Kullanıcı sistemi devre dışı"}), 503

    try:
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"success": False, "error": "Token gerekli."}), 401

        user = users_collection.find_one({"token": token})
        if not user:
            return jsonify({"success": False, "error": "Geçersiz token."}), 401

        data = request.get_json(force=True)
        updates = {}

        if "avatar" in data:
            avatar = data["avatar"].strip()
            if len(avatar) > 500:
                return jsonify({"success": False, "error": "Avatar URL çok uzun."}), 400
            updates["avatar"] = avatar

        if "bio" in data:
            bio = data["bio"].strip()[:200]
            updates["bio"] = bio

        if updates:
            users_collection.update_one(
                {"_id": user["_id"]},
                {"$set": updates}
            )

        return jsonify({"success": True, "message": "Profil güncellendi."})

    except Exception as e:
        logger.error("Profil güncelleme hatası: %s", str(e))
        return jsonify({"success": False, "error": "İşlem başarısız."}), 500



# ── Sitemap.xml (SEO) ────────────────────────────────────────

BASE_URL = "https://mc-cmd.vercel.app"


@app.route("/sitemap.xml")
def sitemap():
    """Dinamik sitemap.xml üretimi. MongoDB'den gönderileri çekerek SEO uyumlu XML döndürür."""
    from xml.etree.ElementTree import Element, SubElement, tostring
    from xml.dom import minidom

    # Statik sayfalar (her zaman var)
    static_pages = [
        {"loc": "/",           "priority": "1.0",  "changefreq": "daily"},
        {"loc": "/community",  "priority": "0.8",  "changefreq": "hourly"},
    ]

    # XML kök elemanı
    urlset = Element("urlset")
    urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")

    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1) Statik sayfaları ekle
    for page in static_pages:
        url_elem = SubElement(urlset, "url")
        SubElement(url_elem, "loc").text = BASE_URL + page["loc"]
        SubElement(url_elem, "priority").text = page["priority"]
        SubElement(url_elem, "changefreq").text = page["changefreq"]
        SubElement(url_elem, "lastmod").text = today_str

    # 2) Dinamik gönderileri MongoDB'den çek (hata durumunda atla)
    if community_collection is not None:
        try:
            posts_cursor = community_collection.find(
                {"approved": True},
                {"_id": 1, "date": 1}
            )

            for post in posts_cursor:
                post_id = str(post.get("_id", ""))
                if not post_id:
                    continue

                # lastmod: date alanı varsa kullan, yoksa bugün
                post_date = post.get("date")
                if post_date and isinstance(post_date, datetime):
                    lastmod = post_date.strftime("%Y-%m-%d")
                else:
                    lastmod = today_str

                url_elem = SubElement(urlset, "url")
                SubElement(url_elem, "loc").text = f"{BASE_URL}/community/post/{post_id}"
                SubElement(url_elem, "priority").text = "0.6"
                SubElement(url_elem, "changefreq").text = "weekly"
                SubElement(url_elem, "lastmod").text = lastmod

        except Exception as e:
            logger.error("Sitemap: MongoDB'den gönderi çekilirken hata: %s", str(e))
            # Hata durumunda statik sayfalar zaten eklenmiş, devam et

    # XML'i pretty-print formatında string'e çevir
    rough_string = tostring(urlset, encoding="unicode")
    reparsed = minidom.parseString(rough_string)
    pretty_xml = reparsed.toprettyxml(indent="  ")

    # İlk satır (XML declaration) ekle
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>'
    # pretty_xml zaten declaration içeriyor, ama encoding olmayabilir
    if not pretty_xml.startswith('<?xml'):
        pretty_xml = xml_declaration + "\n" + pretty_xml
    else:
        # Mevcut declaration'ı düzelt
        pretty_xml = pretty_xml.replace(
            '<?xml version="1.0" ?>',
            xml_declaration
        )

    return Response(
        pretty_xml,
        mimetype="application/xml"
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    logger.info(f"MC Komut Üretici başlatılıyor...")
    logger.info(f"Model: {MODEL}")
    logger.info(f"API Key: {'Yapılandırıldı' if API_KEY else 'EKSİK!'}")
    logger.info(f"Port: {port}")
    logger.info(f"Debug: {debug_mode}")

    app.run(host="0.0.0.0", port=port, debug=debug_mode)
