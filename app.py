import os, json, re
from flask import Flask, request, jsonify, render_template, send_from_directory
import requests
from requests.adapters import HTTPAdapter

app = Flask(__name__)

# ── Static Files ─────────────────────────────────────────────
@app.route('/icon.png')
def icon():    return send_from_directory('.', 'icon.png')
@app.route('/logo.png')
def logo():    return send_from_directory('.', 'logo.png')
@app.route('/kanal.png')
def kanal():   return send_from_directory('.', 'kanal.png')
@app.route('/manifest.json')
def manifest():return send_from_directory('.', 'manifest.json')
@app.route('/sw.js')
def sw():      return send_from_directory('.', 'sw.js')

# ── Cerebras API ──────────────────────────────────────────────
API_KEY = os.environ.get("CEREBRAS_API_KEY")
API_URL = "https://api.cerebras.ai/v1/chat/completions"
MODEL   = "llama3.1-8b"

# ── Sürüm Listesi ─────────────────────────────────────────────
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

COMMAND_TYPES = [
    {"value":"all",       "label":"🚀 Tümü (Otomatik Seç)"},
    {"value":"give",      "label":"🎁 Eşya Verme (/give)"},
    {"value":"summon",    "label":"👾 Yaratık Çağırma (/summon)"},
    {"value":"effect",    "label":"✨ Efekt Verme (/effect)"},
    {"value":"enchant",   "label":"⚔️ Büyüleme (/enchant)"},
    {"value":"tp",        "label":"📍 Işınlanma (/tp)"},
    {"value":"setblock",  "label":"🧱 Blok Koy (/setblock)"},
    {"value":"fill",      "label":"🏗️ Alan Doldur (/fill)"},
    {"value":"execute",   "label":"⚙️ Koşullu Komut (/execute)"},
    {"value":"scoreboard","label":"🔢 Skor Tablosu (/scoreboard)"},
    {"value":"title",     "label":"📺 Ekran Yazısı (/title)"},
    {"value":"tellraw",   "label":"💬 Süslü Sohbet (/tellraw)"},
    {"value":"playsound", "label":"🎵 Ses Oynat (/playsound)"},
    {"value":"particle",  "label":"💨 Partikül (/particle)"},
    {"value":"gamerule",  "label":"📜 Oyun Kuralı (/gamerule)"},
    {"value":"data",      "label":"💾 Veri Düzenle (/data)"},
    {"value":"bossbar",   "label":"👑 Boss Bar (/bossbar)"},
    {"value":"attribute", "label":"📊 Özellik (/attribute)"},
    {"value":"tag",       "label":"🏷️ Etiket (/tag)"},
    {"value":"team",      "label":"👥 Takım (/team)"},
    {"value":"advancement","label":"🏆 Başarım (/advancement)"},
    {"value":"schedule",  "label":"⏱️ Zamanlayıcı (/schedule)"},
    {"value":"loot",      "label":"💰 Loot (/loot)"},
    {"value":"item",      "label":"🎒 Eşya Yönet (/item)"},
    {"value":"complex",   "label":"🔥 Karmaşık Sistem"},
]

# ── Sürüm Detay Bilgileri (Frontend için) ────────────────────
VERSION_DETAILS = {
    "1.8":    {"era":"LEGACY","label":"1.8 — Klasik (Old School)","desc":"Eski NBT syntax • damage değerleri • Enchant ID'leri sayısal"},
    "1.8.9":  {"era":"LEGACY","label":"1.8.9 — Klasik Stabil","desc":"Eski NBT syntax • /give item:damage formatı • En popüler eski versiyon"},
    "1.9":    {"era":"LEGACY","label":"1.9 — Combat Update","desc":"İkili kılıç sistemi • Yeni ok mekaniği • Eski NBT devam ediyor"},
    "1.9.4":  {"era":"LEGACY","label":"1.9.4 — Combat Stabil","desc":"1.9 döneminin en stabil sürümü • PvP meta değişti"},
    "1.10":   {"era":"LEGACY","label":"1.10 — Frostburn","desc":"Polar ayı & magma küp eklendi • Eski syntax devam"},
    "1.10.2": {"era":"LEGACY","label":"1.10.2 — Frostburn Stabil","desc":"Bugfix odaklı sürüm • Eski NBT formatı geçerli"},
    "1.11":   {"era":"LEGACY","label":"1.11 — Exploration","desc":"Haberci, Çoban, Evoker • Observer bloğu • Eski syntax"},
    "1.11.2": {"era":"LEGACY","label":"1.11.2 — Exploration Stabil","desc":"Stabil versiyon • Eski komut sistemi son hali"},
    "1.12":   {"era":"LEGACY","label":"1.12 — World of Color","desc":"Renk sistemi yenilendi • Somut bloklar • Eski syntax son"},
    "1.12.2": {"era":"LEGACY","label":"1.12.2 — Son Eski Sürüm","desc":"⚠️ Eski syntax son versiyonu • NBT ID tabanlı • /give item 1 0 {nbt} formatı"},
    "1.13":   {"era":"MODERN","label":"1.13 — Aquatic (Flattening)","desc":"🔄 DEV REBİRTH! Tüm komutlar değişti • Namespace zorunlu • minecraft:item formatı"},
    "1.13.2": {"era":"MODERN","label":"1.13.2 — Aquatic Stabil","desc":"Yeni syntax ilk stabil • /effect give format • execute yenilendi tamamen"},
    "1.14":   {"era":"MODERN","label":"1.14 — Village & Pillage","desc":"Köy yenilendi • /schedule eklendi • tags sistemi • Datapack desteği güçlendi"},
    "1.14.4": {"era":"MODERN","label":"1.14.4 — V&P Stabil","desc":"Önemli bug düzeltmeleri • Modern syntax stabil hale geldi"},
    "1.15":   {"era":"MODERN","label":"1.15 — Buzzy Bees","desc":"Arılar & bal • Performance iyileştirmeleri • Komut sistemi Modern"},
    "1.15.2": {"era":"MODERN","label":"1.15.2 — Buzzy Bees Stabil","desc":"Bug düzeltmeleri • Modern syntax tam yerleşti"},
    "1.16":   {"era":"MODERN","label":"1.16 — Nether Update","desc":"Nether tamamen yenilendi • Piglins • Yeni biyomlar • Modern komutlar"},
    "1.16.1": {"era":"MODERN","label":"1.16.1","desc":"Nether Update ilk • Soul Speed büyüsü eklendi"},
    "1.16.2": {"era":"MODERN","label":"1.16.2","desc":"Piglin Brute eklendi • Basalt delta biyomu"},
    "1.16.3": {"era":"MODERN","label":"1.16.3","desc":"Stabil Nether • Önemli crash düzeltmeleri"},
    "1.16.4": {"era":"MODERN","label":"1.16.4","desc":"Social Interactions • Çevrimiçi oyuncu engelleme"},
    "1.16.5": {"era":"MODERN","label":"1.16.5 — Nether Stabil","desc":"En popüler 1.16 sürümü • Çoğu mod/server bunu kullanır"},
    "1.17":   {"era":"MODERN","label":"1.17 — Caves & Cliffs Pt.1","desc":"Goat, Axolotl, Glow Squid • /random komutu yok • 1. parti"},
    "1.17.1": {"era":"MODERN","label":"1.17.1 — C&C Stabil","desc":"Önemli düzeltmeler • Modern syntax devam"},
    "1.18":   {"era":"MODERN","label":"1.18 — Caves & Cliffs Pt.2","desc":"Dev yeraltı mağaraları • Yeni ore dağılımı • attribute güncellemeleri"},
    "1.18.1": {"era":"MODERN","label":"1.18.1","desc":"Cave güncelleme • Spawning düzeltmeleri"},
    "1.18.2": {"era":"MODERN","label":"1.18.2 — Cave Stabil","desc":"Stabil mağara • /locate POI desteği • En iyi 1.18"},
    "1.19":   {"era":"MODERN","label":"1.19 — Wild Update","desc":"Mangrove, Deep Dark, Allay • /summon allay • Warden boss"},
    "1.19.1": {"era":"MODERN","label":"1.19.1","desc":"Chat raporlama sistemi • Küçük düzeltmeler"},
    "1.19.2": {"era":"MODERN","label":"1.19.2","desc":"Kritik güvenlik yaması • Modern syntax devam"},
    "1.19.3": {"era":"MODERN","label":"1.19.3","desc":"Camel & Sniffer hazırlığı • Inventory değişiklikleri"},
    "1.19.4": {"era":"MODERN","label":"1.19.4 — Wild Stabil","desc":"En popüler 1.19 • /execute store result güçlendi"},
    "1.20":   {"era":"MODERN","label":"1.20 — Trails & Tales","desc":"Bamboo, Camel, Cherry Grove • Archaeology • Modern syntax"},
    "1.20.1": {"era":"MODERN","label":"1.20.1","desc":"Önemli düzeltmeler • En popüler 1.20 başlangıcı"},
    "1.20.2": {"era":"MODERN","label":"1.20.2","desc":"Protocol değişikliği • Birden fazla oyuncu selector"},
    "1.20.4": {"era":"MODERN","label":"1.20.4","desc":"Son eski item syntax • NBT tabanlı son versiyon • items yakında değişecek"},
    "1.20.5": {"era":"COMPONENTS","label":"1.20.5 — COMPONENTS BAŞLADI! ⚡","desc":"🚨 BÜYÜK DEĞİŞİKLİK! NBT → Components • /give @p item[component=...] formatı"},
    "1.20.6": {"era":"COMPONENTS","label":"1.20.6 — Components Stabil","desc":"Components format oturdu • minecraft:enchantments={levels:{...}} • Eski NBT çalışmaz!"},
    "1.21":   {"era":"COMPONENTS","label":"1.21 — Tricky Trials","desc":"Trial Chambers • Mace silahı • Breeze mob • Components syntax zorunlu"},
    "1.21.1": {"era":"COMPONENTS","label":"1.21.1","desc":"Trial fix • Components devam • Wind Charge silahı"},
    "1.21.4": {"era":"COMPONENTS","label":"1.21.4 — Bundles of Bravery","desc":"Bundle eklendi • Yeni item özellikleri • Components genişledi"},
    "1.21.5": {"era":"COMPONENTS","label":"1.21.5","desc":"Performance • Components tam stabil • Yeni potion efektleri"},
    "1.21.8": {"era":"COMPONENTS","label":"1.21.8","desc":"Güncel stabil • Components tam oturdu • Tüm yeni itemlar"},
    "1.21.11":{"era":"COMPONENTS","label":"1.21.11 — En Güncel ✅","desc":"En son sürüm • Components syntax tam • pack_format güncel"},
}

def get_era(version):
    v = version.split(".")
    minor = int(v[1]) if len(v) > 1 and v[1].isdigit() else 8
    patch = int(v[2]) if len(v) > 2 and v[2].isdigit() else 0
    if minor < 13: return "LEGACY"
    if minor < 20 or (minor == 20 and patch < 5): return "MODERN"
    return "COMPONENTS"

# ── System Prompts ────────────────────────────────────────────

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
  - /scoreboard objectives add kills minecraft.killed:minecraft.player "☠ Kill Sayısı"
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
/team: /team add red "§cKırmızı Takım" | /team join red @p | /team modify red color red
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
  - /give @p minecraft:netherite_sword[minecraft:custom_name='{"text":"⚔ GODSLAYER","color":"dark_red","bold":true,"italic":false}'] 1
  
  LORE:
  - /give @p minecraft:diamond[minecraft:lore=['{"text":"✦ Efsanevi Parça","color":"light_purple","italic":false}','{"text":"Void\'dan dövülmüş","color":"dark_purple","italic":true}']] 1
  
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
  - /give @p minecraft:netherite_sword[minecraft:enchantments={levels:{"minecraft:sharpness":10,"minecraft:fire_aspect":5,"minecraft:looting":10,"minecraft:mending":1,"minecraft:unbreaking":10}},minecraft:custom_name='{"text":"⚔ GODSLAYER","color":"dark_red","bold":true,"italic":false}',minecraft:lore=['{"text":"Void ile dövülmüş","color":"dark_purple","italic":true}'],minecraft:unbreakable={show_in_tooltip:false},minecraft:enchantment_glint_override=true] 1

/effect: Same as modern but new 1.21 effects:
  - /effect give @a minecraft:wind_charged 60 1 true
  - /effect give @a minecraft:weaving 60 1 true
  - /effect give @a minecraft:oozing 60 1 true
  - /effect give @a minecraft:infested 60 1 true

/summon: Still uses NBT for entities:
  - /summon minecraft:zombie ~ ~1 ~ {Health:200.0f,Attributes:[{"Name":"minecraft:generic.max_health","Base":200.0},{"Name":"minecraft:generic.scale","Base":3.0},{"Name":"minecraft:generic.attack_damage","Base":20.0},{"Name":"minecraft:generic.armor","Base":20.0},{"Name":"minecraft:generic.movement_speed","Base":0.4},{"Name":"minecraft:generic.follow_range","Base":64.0}],CustomName:'{"text":"TITAN BOSS","color":"dark_red","bold":true}',CustomNameVisible:1b,PersistenceRequired:1b,NoAI:0b,Tags:["boss","titan"],HandItems:[{id:"minecraft:netherite_sword",count:1,components:{"minecraft:enchantments":{levels:{"minecraft:sharpness":10}}}},{}]}

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
    extra = f"\n\nGenerating for Minecraft Java Edition {version}. {detail.get('desc','')}\nAll commands MUST be 100% correct for version {version} specifically. Return ONLY valid JSON."
    return base + extra

def call_api(msgs):
    s = requests.Session()
    s.mount('https://', HTTPAdapter(max_retries=1))
    resp = s.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": MODEL,
            "messages": msgs,
            "temperature": 0.15,
            "max_tokens": 3000,
            "stream": False
        },
        timeout=(15, 90)
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]

def parse_response(raw):
    raw = raw.strip()
    # Strip markdown fences
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```\s*$', '', raw)
    raw = raw.strip()
    
    # Direct parse
    try:
        return json.loads(raw)
    except:
        pass
    
    # Fix unescaped newlines in strings
    def fix_json(text):
        result = []
        in_string = False
        i = 0
        while i < len(text):
            c = text[i]
            if c == '\\' and i + 1 < len(text):
                result.append(c)
                result.append(text[i+1])
                i += 2
                continue
            if c == '"':
                in_string = not in_string
            elif in_string and c == '\n':
                result.append('\\n')
                i += 1
                continue
            elif in_string and c == '\r':
                i += 1
                continue
            result.append(c)
            i += 1
        return ''.join(result)
    
    try:
        return json.loads(fix_json(raw))
    except:
        pass
    
    # Extract JSON object
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        try:
            return json.loads(fix_json(m.group(0)))
        except:
            pass
    
    raise ValueError(f"JSON parse failed. Raw: {raw[:200]}")

@app.route("/")
def index():
    return render_template("index.html",
        versions=VERSIONS,
        command_types=COMMAND_TYPES,
        version_details=VERSION_DETAILS
    )

@app.route("/ping")
def ping():
    return jsonify({"ok": True, "model": MODEL, "api": "Cerebras"})

@app.route("/version-info/<version>")
def version_info(version):
    detail = VERSION_DETAILS.get(version, {})
    era = get_era(version)
    return jsonify({"era": era, **detail})

@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    idea = data.get("idea", "").strip()
    version = data.get("version", "1.21.4")
    platform = data.get("platform", "Java")
    cmd_type = data.get("command_type", "all")

    if not idea:
        return jsonify({"success": False, "error": "Bir fikir yazmalısın!"}), 400
    if not version:
        return jsonify({"success": False, "error": "Sürüm seçilmedi!"}), 400

    era = get_era(version)
    type_hint = f" Komut türü: {cmd_type}." if cmd_type != "all" else ""
    
    user_msg = (
        f"Minecraft Java Edition {version} için şu isteği komutlara dönüştür: {idea}.{type_hint} "
        f"Sürüm erası: {era}. Tüm komutlar bu sürüm için %100 doğru ve çalışır olmalı."
    )

    try:
        raw = call_api([
            {"role": "system", "content": get_system(version)},
            {"role": "user",   "content": user_msg}
        ])
    except requests.exceptions.Timeout:
        return jsonify({"success": False, "error": "API zaman aşımına uğradı. Tekrar dene."}), 504
    except requests.exceptions.HTTPError as e:
        return jsonify({"success": False, "error": f"API Hatası: {str(e)[:100]}"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:200]}), 500

    try:
        parsed = parse_response(raw)
    except Exception as e:
        return jsonify({"success": False, "error": f"Yanıt formatlanamadı: {str(e)[:100]}"}), 500

    detail = VERSION_DETAILS.get(version, {})
    
    return jsonify({
        "success": True,
        "version": version,
        "era": era,
        "era_label": detail.get("label", version),
        "platform": platform,
        "command_type": cmd_type,
        "commands": parsed.get("commands", []),
        "explanation": parsed.get("explanation", ""),
        "execution_order": parsed.get("execution_order", ""),
        "multiple_commands": parsed.get("multiple_commands", False),
        "requires_datapack": parsed.get("requires_datapack", False),
        "requires_command_block": parsed.get("requires_command_block", False),
        "tips": parsed.get("tips", []),
        "common_mistakes": parsed.get("common_mistakes", [])
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
