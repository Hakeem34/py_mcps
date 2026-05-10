import os
import sys
import json
import requests
import dataclasses
import tempfile
import subprocess
from mcp.server.fastmcp import FastMCP

g_speakers = []

@dataclasses.dataclass
class Speaker:
    name: str
    speaker_uuid: str
    style_ids: dict[str, int]  # スタイル名とスタイルIDのマッピング

# 1. 接続設定（環境に合わせて書き換えてください）
VOICEVOX_URL = 'http://127.0.0.1:50021/'


# MCPサーバーのインスタンス作成
mcp = FastMCP("VoiceVoxExplorer")

# VoiceVox APIクライアントの初期化
voicevox = requests.Session()
voicevox.base_url = VOICEVOX_URL


def get_style_id(speaker_name, style_name) -> int:
    # speaker_nameとstyle_nameからとstyle_idを取得
    target_speaker = None
    for speaker in g_speakers:
        if speaker.name == speaker_name:
            target_speaker = speaker
    
    if not target_speaker:
        print(f"Speaker not found: {speaker_name}", file=sys.stderr)
        return -1

    print(target_speaker.style_ids, file=sys.stderr)
    style_id = ""
    for name, id in target_speaker.style_ids.items():
#       print(f"{name}:{id}", file=sys.stderr)
        if name == style_name:
            style_id = id
            break

    if style_id == "":
        style_id = next(iter(target_speaker.style_ids.values()))
        print(f"Style not found for {speaker_name}: {style_name}, use {next(iter(target_speaker.style_ids.keys()))}", file=sys.stderr)

    return style_id


def get_audio_query(text: str, style_id: int) -> str:
    """
    テキストとスタイルIDからVoiceVoxのaudio_queryを取得します
    """
    try:
        response = voicevox.post(
            voicevox.base_url + 'audio_query',
            params={'text': text, 'speaker': style_id}
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error getting audio query: {e}", file=sys.stderr)
        return f"Error getting audio query: {e}"

def synth_audio(query, style_id):
    """
    クエリをもとに音声データを生成（バイナリを返す）
    """
    try:
        synth_res = voicevox.post(
            voicevox.base_url + 'synthesis',
            params={"speaker": style_id},
            data=json.dumps(query)
        )
        synth_res.raise_for_status()
        return synth_res.content 
    except requests.RequestException as e:
        print(f"Error synth_audio: {e}", file=sys.stderr)
        return None


def play_audio(text, audio_data):
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".wav"
        ) as fp:
            temp_path = fp.name
            fp.write(audio_data)

        print(f"ffplay start: {temp_path}", file=sys.stderr)

        result = subprocess.run(
            [
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                temp_path
            ]
        )

        print(f"ffplay end: {result.returncode}", file=sys.stderr)

        return f"「{text}」を再生しました"

    except Exception as e:
        print(f"Error play_audio: {repr(e)}", file=sys.stderr)
        return f"「{text}」の再生に失敗しました"

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as e:
                print(f"temp cleanup error: {e}", file=sys.stderr)


@mcp.tool()
def speech_by_voicevox(text: str, speaker_name: str = "四国めたん", style_name: str = "ノーマル") -> str:
    """
    VoiceVoxでtextの内容で音声データを生成し、再生します
    """
    print(f"Generating speech for text: {text}, speaker: {speaker_name}, style: {style_name}", file=sys.stderr)
    result = get_style_id(speaker_name, style_name)
    if result == -1:
        return f"{speaker_name}が見つかりません"

    style_id = int(result)
    print(f"Generating speech for speaker {speaker_name} with style {style_id}...", file=sys.stderr)
    query = get_audio_query(text, style_id)
#   print(query)

    # 音声バイナリを取得
    audio_data = synth_audio(query, style_id)
    
    if audio_data:
        return play_audio(text, audio_data)
    else:
        return f"「{text}」の再生に失敗しました"

    print(f"「{text}」を再生しました", file=sys.stderr)
    

@mcp.tool()
def get_voicevox_speakers() -> str:
    """
    VoiceVoxのspeaker名とstyle名の一覧を取得します。
    """
    global g_speakers
    
    if not g_speakers:
        print("Fetching speakers from VoiceVox...", file=sys.stderr)
        try:
            response = voicevox.get(voicevox.base_url + 'speakers')
            response.raise_for_status()
            speakers = response.json()
        except requests.RequestException as e:
            print(f"Error fetching speakers: {e}", file=sys.stderr)
            return f"Error fetching speakers: {e}"

        for speaker_data in speakers:
            speaker = Speaker(
                name=speaker_data['name'],
                speaker_uuid=speaker_data['speaker_uuid'],
                style_ids={style['name']: style['id'] for style in speaker_data['styles']}
            )
            g_speakers.append(speaker)
            print(f"{speaker.name}:" + ",".join(speaker.style_ids.keys()), file=sys.stderr)
            
    result = ""
    for speaker in g_speakers:
        result += f"{speaker.name}:" + ",".join(speaker.style_ids.keys()) + "\n"

#   print(f"{result}", file=sys.stderr)
    return result


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--utf8":
        sys.stderr.reconfigure(encoding="utf-8")

    get_voicevox_speakers()  # ここでツールを呼び出してみる
#   speech_by_voicevox("おはようなのだ", "ずんだもん")
#   speech_by_voicevox("おはようなのだ", "ずんだもん", "あまあま")
    mcp.run()
    pass


if __name__ == "__main__":
    main()
