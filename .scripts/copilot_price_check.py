import os
import re
import sqlite3
import getpass
import datetime
import json
import dataclasses
import csv

@dataclasses.dataclass
class ModelMetadata:
    name: str
    priceCategory: str = ""
    inputCost: int = 0
    outputCost: int = 0
    cacheCost: int = 0
    cacheWriteCost: int = 0
    longContextInputCost: int = 0
    longContextOutputCost: int = 0
    longContextCacheCost: int = 0
    longContextCacheWriteCost: int = 0
    maxInputTokens: int = 0
    maxOutputTokens: int = 0
    vision: bool = False


g_model_info = []

user = getpass.getuser()
db_path = rf"{os.getenv('APPDATA')}\Code\User\globalStorage\state.vscdb"

now = datetime.datetime.now()
time_stamp = now.strftime("%Y%m%d_%H%M%S")
log_file_path = f"state_{user}_{time_stamp}.log"
with open(log_file_path, "w", encoding="utf-8") as log_file:
    log_file.write(f"Log file created on: {now}\n")
    log_file.write(f"User: {user}\n")
    log_file.write(f"Database path: {db_path}\n\n")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT key, value
            FROM ItemTable
        """)

        for key, value in cursor.fetchall():
#           log_file.write("=" * 80 + "\n")
#           log_file.write(f"KEY: {key}\n")
#           log_file.write(f"VALUE: {value}\n")
            if result := re.match(r"chat.cachedLanguageModels.v(\d+)", key):
                if result.group(1) == "2":
                    log_file.write(f"Matched key: {result.group(0)}\n")
                    # log_file.write(f"Value: {value}\n")
                    json_value = json.loads(value)
#                   print(json.dumps(json_value, indent=4), file=log_file)
                    for model in json_value:
                        metadata = model.get("metadata")
                        name = metadata.get("name")
                        byok = metadata.get("isBYOK")
#                       print(f"Model Name: {name}, isBYOK: {byok}", file=log_file)
                        if metadata.get("name") != "Auto" and metadata.get("isBYOK") != True:
                            model_info = ModelMetadata(
                                name=metadata.get("name"),
                            )

                            capabilities = metadata.get("capabilities", [])
                            model_info.priceCategory = metadata.get("priceCategory", "")
                            model_info.inputCost = metadata.get("inputCost", 0)
                            model_info.outputCost = metadata.get("outputCost", 0)
                            model_info.cacheCost = metadata.get("cacheCost", 0)
                            model_info.cacheWriteCost = metadata.get("cacheWriteCost", 0)
                            model_info.longContextInputCost = metadata.get("longContextInputCost", 0)
                            model_info.longContextOutputCost = metadata.get("longContextOutputCost", 0)
                            model_info.longContextCacheCost = metadata.get("longContextCacheCost", 0)
                            model_info.longContextCacheWriteCost = metadata.get("longContextCacheWriteCost", 0)
                            model_info.maxInputTokens = metadata.get("maxInputTokens", 0)
                            model_info.maxOutputTokens = metadata.get("maxOutputTokens", 0)
                            model_info.vision = capabilities.get("vision", False)
                            g_model_info.append(model_info)
                            print(f"[{model_info.name:<20}]:{model_info.priceCategory:<10}, {model_info.inputCost}, {model_info.outputCost}, {model_info.cacheCost}, {model_info.cacheWriteCost}, {model_info.longContextInputCost}, {model_info.longContextOutputCost}, {model_info.longContextCacheCost}, {model_info.longContextCacheWriteCost}, vision: {model_info.vision}", file=log_file)
    finally:
        conn.close()

    with open(f"model_info_{time_stamp}.csv", "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Name", "Price Category", "Input Cost", "Output Cost", "Cache Cost", "Cache Write Cost", "Long Context Input Cost", "Long Context Output Cost", "Long Context Cache Cost", "Long Context Cache Write Cost", "Max Input Tokens", "Max Output Tokens", "Vision"])

        for model_info in g_model_info:
            if model_info.priceCategory != "":
                writer.writerow([
                model_info.name,
                model_info.priceCategory,
                model_info.inputCost,
                model_info.outputCost,
                model_info.cacheCost,
                model_info.cacheWriteCost,
                model_info.longContextInputCost,
                model_info.longContextOutputCost,
                model_info.longContextCacheCost,
                model_info.longContextCacheWriteCost,
                model_info.maxInputTokens,
                model_info.maxOutputTokens,
                model_info.vision
                ])
