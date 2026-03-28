import os, json, re
from flask import Flask, request, jsonify, render_template, send_from_directory # Buraya send_from_directory ekledik
import requests
from requests.adapters import HTTPAdapter

app = Flask(__name__)

# --- BURADAN BAŞLA ---
@app.route('/icon.png')
def icon():
    return send_from_directory('.', 'icon.png')

@app.route('/logo.png')
def logo():
    return send_from_directory('.', 'logo.png')

@app.route('/kanal.png')
def kanal():
    return send_from_directory('.', 'kanal.png')

@app.route('/manifest.json')
def manifest():
    return send_from_directory('.', 'manifest.json')

@app.route('/sw.js')
def sw():
    return send_from_directory('.', 'sw.js')

# API Ayarları (Güvenli Yöntem)
API_KEY = os.environ.get("OPENROUTER_API_KEY")
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL   = "google/gemini-2.0-flash-001"

VERSIONS = [
    "1.8","1.8.9","1.9","1.9.4","1.10","1.10.2",
    "1.11","1.11.2","1.12","1.12.2",
    "1.13","1.13.2","1.14","1.14.4","1.15","1.15.2",
    "1.16","1.16.5","1.17","1.17.1","1.18","1.18.2",
    "1.19","1.19.4","1.20","1.20.1","1.20.4",
    "1.20.5","1.20.6","1.21","1.21.1","1.21.4",
    "1.21.5","1.21.8","1.21.11"
]

COMMAND_TYPES = [
    "give","summon","effect","enchant",
    "tp / teleport","setblock","fill","execute",
    "scoreboard","title","tellraw","playsound",
    "particle","gamemode","gamerule",
    "weather / time","kill","tag","team",
    "bossbar","advancement","data","attribute",
    "loot","schedule","custom / complex system"
]

def get_era(version):
    v = version.split(".")
    minor = int(v[1]) if len(v) > 1 and v[1].isdigit() else 8
    patch = int(v[2]) if len(v) > 2 and v[2].isdigit() else 0
    if minor < 13: return "LEGACY"
    if minor < 20 or (minor == 20 and patch < 5): return "MODERN"
    return "COMPONENTS"

SYSTEM_LEGACY = """You are a world-class Minecraft command expert. ERA: LEGACY (1.8-1.12.2)
LANGUAGE RULE: ALL Minecraft commands MUST be in English. NEVER translate commands to Turkish or any other language. Descriptions and explanations can be in Turkish but commands must ALWAYS be in English.
COMMAND BLOCK RULE: For EVERY command in the output, you MUST specify where_to_run as one of these EXACT values:
  "REPEATING COMMAND BLOCK (Always Active)" - for commands that run every tick
  "CHAIN COMMAND BLOCK (Always Active)" - for commands that chain after another
  "IMPULSE COMMAND BLOCK" - for one-time commands triggered manually
  "CHAT / CONSOLE" - for one-time commands typed in chat

GIVE: /give <player> <item> [count] [damage] [nbt]
  Ex: /give @p minecraft:diamond_sword 1 0 {ench:[{id:16,lvl:5},{id:20,lvl:2}]}
  Enchant IDs: Sharpness=16,Smite=17,Knockback=19,FireAspect=20,Looting=21,Efficiency=32,SilkTouch=33,Unbreaking=35,Fortune=35,Power=48,Punch=49,Flame=50,Infinity=51,Protection=0,FireProt=1,FeatherFalling=4,Thorns=7,Respiration=6,DepthStrider=8,Mending=70
  Custom name: /give @p minecraft:stick 1 0 {display:{Name:"Magic Stick"}}
EFFECT: /effect <player> <effectID> [seconds] [amplifier]
  IDs: Speed=1,Haste=3,Strength=5,InstantHealth=6,InstantDamage=7,JumpBoost=8,Regeneration=10,Resistance=11,FireResistance=12,Invisibility=14,Blindness=15,NightVision=16,Hunger=17,Weakness=18,Poison=19,Wither=20,Absorption=22
SUMMON: /summon <EntityType> [x] [y] [z] [nbt]
  CamelCase: Zombie,Skeleton,Creeper,ArmorStand,EnderDragon
  Ex: /summon Zombie ~ ~1 ~ {CustomName:"Boss",Health:200f,Attributes:[{Name:"generic.maxHealth",Base:200}]}
EXECUTE (old): /execute <entity> <x> <y> <z> <command>
TESTFOR: /testfor @a[r=10]
SCOREBOARD: /scoreboard objectives add kills totalKillCount | /scoreboard players set @p obj 10
TITLE: /title @a title {"text":"Hello","color":"red"}
KILL: /kill @e[type=Zombie,r=20]

OUTPUT JSON ONLY (no markdown, no translation of commands):
{
  "commands": [{"command":"/exact english command","description":"Türkçe açıklama","notes":"Uyarılar","where_to_run":"REPEATING COMMAND BLOCK (Always Active)"}],
  "explanation": "Türkçe genel açıklama",
  "execution_order": "Sıralama açıklaması",
  "multiple_commands": false,
  "requires_datapack": false,
  "requires_command_block": false,
  "tips": ["ipucu1"],
  "common_mistakes": ["hata1"]
}"""

SYSTEM_MODERN = """You are a world-class Minecraft command expert. ERA: MODERN (1.13-1.20.4)
LANGUAGE RULE: ALL Minecraft commands MUST be in English. NEVER translate commands to Turkish or any other language. Descriptions and explanations can be in Turkish but commands must ALWAYS be in English.
COMMAND BLOCK RULE: For EVERY command in the output, you MUST specify where_to_run as one of these EXACT values:
  "REPEATING COMMAND BLOCK (Always Active)" - for commands that run every tick
  "CHAIN COMMAND BLOCK (Always Active)" - for commands that chain after another
  "IMPULSE COMMAND BLOCK" - for one-time commands triggered manually
  "CHAT / CONSOLE" - for one-time commands typed in chat

GIVE: /give @p minecraft:diamond_sword{Enchantments:[{id:"minecraft:sharpness",lvl:5},{id:"minecraft:fire_aspect",lvl:2}]}
  Custom name: /give @p minecraft:stick{display:{Name:'{"text":"Magic","color":"gold"}'}}
  Lore: /give @p minecraft:stick{display:{Lore:['[{"text":"Lore","color":"gray"}]']}}
EFFECT: /effect give @a minecraft:speed 30 1 true | /effect clear @a minecraft:speed
  Names: minecraft:speed,minecraft:strength,minecraft:regeneration,minecraft:resistance,minecraft:fire_resistance,minecraft:night_vision,minecraft:invisibility,minecraft:slowness,minecraft:weakness,minecraft:poison,minecraft:wither,minecraft:levitation,minecraft:glowing,minecraft:absorption,minecraft:nausea,minecraft:blindness,minecraft:jump_boost,minecraft:haste,minecraft:mining_fatigue,minecraft:hunger
SUMMON: /summon minecraft:zombie ~ ~1 ~ {Health:200f,CustomName:'{"text":"Boss","color":"red"}',CustomNameVisible:1b,PersistenceRequired:1b,Attributes:[{Name:"generic.max_health",Base:200.0},{Name:"generic.movement_speed",Base:0.4},{Name:"generic.attack_damage",Base:15.0},{Name:"generic.armor",Base:10.0}],Tags:["boss"]}
EXECUTE:
  execute as @a at @s if block ~ ~-1 ~ minecraft:grass_block run effect give @s minecraft:speed 5 1
  execute as @e[type=minecraft:zombie,tag=boss] at @s if entity @a[distance=..5] run effect give @a[distance=..5] minecraft:slowness 3 2 true
  execute store result score @s hp run data get entity @s Health 1
  execute if score @s kills matches 10.. run title @s title {"text":"10 Kills!","color":"gold"}
  execute as @a[scores={kills=5..}] run title @s actionbar {"text":"5 kills!"}
SCOREBOARD: /scoreboard objectives add kills dummy | /scoreboard objectives setdisplay sidebar kills | /scoreboard players add @a kills 1
DATA: /data get entity @s Health | /data modify entity @s CustomName set value '{"text":"New"}' | /data merge entity @s {Glowing:1b,NoAI:1b}
TP: /tp @a 0 64 0 | /teleport @p ~ ~5 ~ facing 0 64 0
FILL: /fill ~-5 ~-1 ~-5 ~5 ~5 ~5 minecraft:obsidian outline
TITLE: /title @a title {"text":"TITLE","color":"red","bold":true} | /title @a actionbar {"text":"bar"}
TELLRAW: /tellraw @a [{"text":"Hello "},{"selector":"@p"},{"text":"! Score: "},{"score":{"name":"@p","objective":"kills"}}]
PARTICLE: /particle minecraft:flame ~ ~1 ~ 0.5 0.5 0.5 0.1 50
BOSSBAR: /bossbar add ns:id {"text":"Name"} | /bossbar set ns:id value 80 | /bossbar set ns:id color red
TAG: /tag @p add vip | @e[tag=vip]
ATTRIBUTE: /attribute @p minecraft:generic.max_health base set 40

OUTPUT JSON ONLY (no markdown, no translation of commands):
{
  "commands": [{"command":"/exact english command","description":"Türkçe açıklama","notes":"Uyarılar","where_to_run":"REPEATING COMMAND BLOCK (Always Active)"}],
  "explanation": "Türkçe genel açıklama",
  "execution_order": "Sıralama açıklaması",
  "multiple_commands": false,
  "requires_datapack": false,
  "requires_command_block": false,
  "tips": ["ipucu1"],
  "common_mistakes": ["hata1"]
}"""

SYSTEM_COMPONENTS = """You are a world-class Minecraft command expert. ERA: COMPONENTS (1.20.5+)
LANGUAGE RULE: ALL Minecraft commands MUST be in English. NEVER translate commands to Turkish or any other language. Descriptions and explanations can be in Turkish but commands must ALWAYS be in English.
COMMAND BLOCK RULE: For EVERY command in the output, you MUST specify where_to_run as one of these EXACT values:
  "REPEATING COMMAND BLOCK (Always Active)" - for commands that run every tick
  "CHAIN COMMAND BLOCK (Always Active)" - for commands that chain after another
  "IMPULSE COMMAND BLOCK" - for one-time commands triggered manually
  "CHAT / CONSOLE" - for one-time commands typed in chat

GIVE — COMPONENTS FORMAT (NOT old nbt curly braces):
  Enchantments: /give @p minecraft:netherite_sword[minecraft:enchantments={levels:{minecraft:sharpness:10,minecraft:fire_aspect:5,minecraft:looting:10,minecraft:mending:1,minecraft:unbreaking:10}}] 1
  Custom Name:  /give @p minecraft:stick[minecraft:custom_name='{"text":"Magic Stick","color":"light_purple","italic":false}'] 1
  Lore:         /give @p minecraft:diamond[minecraft:lore=['{"text":"Rare Item","color":"aqua"}','{"text":"Handle with care","color":"gray"}']] 1
  Unbreakable:  /give @p minecraft:diamond_sword[minecraft:unbreakable={show_in_tooltip:false}] 1
  Glint:        /give @p minecraft:nether_star[minecraft:enchantment_glint_override=true] 1
  Custom Data:  /give @p minecraft:nether_star[minecraft:custom_data={boss_egg:1,tier:3}] 1
  Custom Model: /give @p minecraft:stick[minecraft:custom_model_data={floats:[1.0]}] 1
  Hide Tooltip: /give @p minecraft:diamond[minecraft:hide_tooltip={}] 1
  FULL OP SWORD: /give @p minecraft:netherite_sword[minecraft:enchantments={levels:{minecraft:sharpness:10,minecraft:fire_aspect:5,minecraft:looting:10,minecraft:mending:1,minecraft:unbreaking:10}},minecraft:custom_name='{"text":"GODSLAYER","color":"dark_red","bold":true,"italic":false}',minecraft:lore=['{"text":"Forged in void","color":"dark_purple","italic":true}'],minecraft:unbreakable={show_in_tooltip:false},minecraft:enchantment_glint_override=true] 1
EFFECT: /effect give @a minecraft:speed 30 2 true | /effect clear @p
  New 1.21+: minecraft:wind_charged,minecraft:weaving,minecraft:oozing,minecraft:infested
SUMMON: /summon minecraft:zombie ~ ~1 ~ {Health:200.0f,Attributes:[{Name:"minecraft:generic.max_health",Base:200.0},{Name:"minecraft:generic.scale",Base:3.0},{Name:"minecraft:generic.attack_damage",Base:15.0},{Name:"minecraft:generic.armor",Base:10.0},{Name:"minecraft:generic.movement_speed",Base:0.4}],CustomName:'{"text":"MEGA BOSS","color":"red","bold":true}',CustomNameVisible:1b,PersistenceRequired:1b,Tags:["boss","mega"]}
EXECUTE (same as modern + random):
  execute as @a at @s if block ~ ~-1 ~ minecraft:grass_block run effect give @s minecraft:speed 5 1 true
  execute store result score @s hp run data get entity @s Health 1
  execute if score @s kills matches 10.. run title @s title {"text":"10 Kills!","color":"gold","bold":true}
  execute store result score #r rand run random value 0..5
SCOREBOARD: same as modern
ATTRIBUTE: /attribute @p minecraft:generic.max_health base set 40 | /attribute @p minecraft:generic.movement_speed base set 0.2
ITEM: /item replace entity @p armor.head with minecraft:diamond_helmet[minecraft:enchantments={levels:{minecraft:protection:10}}]
BOSSBAR: /bossbar add boss:health {"text":"Boss HP","color":"red"} | /bossbar set boss:health visible true
SCHEDULE: /schedule function namespace:func 100t append | /schedule function namespace:func 5s replace
TAG/TEAM/ADVANCEMENT/TITLE/TELLRAW/PARTICLE: same as modern era

OUTPUT JSON ONLY (no markdown, no translation of commands):
{
  "commands": [{"command":"/exact english command","description":"Türkçe açıklama","notes":"Uyarılar","where_to_run":"REPEATING COMMAND BLOCK (Always Active)"}],
  "explanation": "Türkçe genel açıklama",
  "execution_order": "Sıralama açıklaması",
  "multiple_commands": false,
  "requires_datapack": false,
  "requires_command_block": false,
  "tips": ["ipucu1"],
  "common_mistakes": ["hata1"]
}"""

def get_system(version):
    era = get_era(version)
    prompts = {"LEGACY": SYSTEM_LEGACY, "MODERN": SYSTEM_MODERN, "COMPONENTS": SYSTEM_COMPONENTS}
    return prompts[era] + f"\n\nYou are generating for Minecraft {version}. Commands MUST be 100% correct English syntax for this exact version."

def call_api(msgs):
    s = requests.Session()
    s.mount('https://', HTTPAdapter(max_retries=1))
    resp = s.post(API_URL,
        headers={"Authorization":f"Bearer {API_KEY}","Content-Type":"application/json","Connection":"close","HTTP-Referer":"http://localhost:5000","X-Title":"MC Command Generator"},
        json={"model":MODEL,"messages":msgs,"temperature":0.2,"max_tokens":4096,"stream":False},
        timeout=(15,120))
    resp.raise_for_status()
    s.close()
    return resp.json()["choices"][0]["message"]["content"]

def parse(raw):
    raw = raw.strip()
    raw = re.sub(r'^```(?:json)?\s*','',raw)
    raw = re.sub(r'\s*```\s*$','',raw)
    raw = raw.strip()
    try: return json.loads(raw)
    except: pass
    def fix(t):
        res,ins,i=[],False,0
        while i<len(t):
            c=t[i]
            if c=='\\'and i+1<len(t): res+=[c,t[i+1]];i+=2;continue
            if c=='"': ins=not ins
            elif ins and c=='\n': res.append('\\n');i+=1;continue
            res.append(c);i+=1
        return ''.join(res)
    try: return json.loads(fix(raw))
    except: pass
    m=re.search(r'\{.*\}',raw,re.DOTALL)
    if m:
        try: return json.loads(fix(m.group(0)))
        except: pass
    raise ValueError(f"parse failed: {raw[:150]}")

@app.route("/")
def index():
    # Sürümleri ve komut tiplerini HTML'e gönderiyoruz ki dropdown'lar dolsun
    return render_template(
        "index.html", 
        versions=VERSIONS, 
        command_types=COMMAND_TYPES
    )
@app.route("/ping")
def ping():
    return jsonify({"ok":True,"model":MODEL})

@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    idea = data.get("idea")
    version = data.get("version")
    platform = data.get("platform", "Java")
    cmd_type = data.get("command_type", "All")

    if not idea or not version:
        return jsonify({"success": False, "error": "Eksik veri!"}), 400

    # Yapay zekaya giden mesajı platform ve türe göre güncelledik
    user_msg = (
        f"Minecraft {platform} Edition {version} sürümü için şu fikirle ilgili komutlar üret: {idea}. "
        f"Seçilen kategori: {cmd_type}. Lütfen her komutun 'place' (Sohbet veya Komut Bloğu) bilgisini "
        f"JSON formatında 'commands' listesi içinde döndür."
    )

    try:
        raw = call_api([
            {"role": "system", "content": get_system(version)},
            {"role": "user", "content": user_msg}
        ])
        print(f"[CMD] raw_len={len(raw)}")
    except requests.exceptions.Timeout:
        return jsonify({"error": "API zaman aşımı. Tekrar dene."}), 504
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

    try:
        parsed = parse(raw)
    except Exception as e:
        return jsonify({"error": "Parse hatası: " + str(e)[:150]}), 500

    return jsonify({
        "success": True,
        "version": version,
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
    # Render.com veya Termux için uyumlu port ayarı
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)