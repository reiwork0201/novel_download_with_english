import os
import re
import time
import requests
import subprocess
from bs4 import BeautifulSoup
from g4f.client import Client

client = Client()

BASE_URL = "https://kakuyomu.jp"
HISTORY_FILE = "カクヨムダウンロード経歴.txt"
LOCAL_HISTORY_PATH = f"/tmp/{HISTORY_FILE}"
REMOTE_HISTORY_PATH = f"drive:{HISTORY_FILE}"
DOWNLOAD_DIR_BASE = "/tmp/kakuyomu_dl"

os.makedirs(DOWNLOAD_DIR_BASE, exist_ok=True)


def translate_text(japanese_text):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": (
                    "You are a professional translator specializing in Japanese fantasy novels. "
                    "Translate the following passage into natural, expressive English that preserves the original tone, atmosphere, and character voices. "
                    "Do NOT translate the title or include any headers. Output only the translated body."
                )
            }, {
                "role": "user",
                "content": japanese_text
            }],
            web_search=False
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[Translation failed: {e}]"


def load_history():
    if not os.path.exists(LOCAL_HISTORY_PATH):
        subprocess.run(['rclone', 'copyto', REMOTE_HISTORY_PATH, LOCAL_HISTORY_PATH], check=False)

    history = {}
    if os.path.exists(LOCAL_HISTORY_PATH):
        with open(LOCAL_HISTORY_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                match = re.match(r'(https?://[^\s|]+)\s*\|\s*(\d+)', line.strip())
                if match:
                    url, last = match.groups()
                    history[url] = int(last)
    return history


def save_history(history):
    with open(LOCAL_HISTORY_PATH, 'w', encoding='utf-8') as f:
        for url, last in history.items():
            f.write(f'{url}  |  {last}\n')
    subprocess.run(['rclone', 'copyto', LOCAL_HISTORY_PATH, REMOTE_HISTORY_PATH], check=True)


def get_novel_title(novel_url):
    response = requests.get(novel_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    title_tag = soup.find("title")
    if title_tag:
        title_text = title_tag.text.strip()
        title_text = re.sub(r'\s*[-ー]?\s*カクヨム.*$', '', title_text)
        return title_text
    else:
        return "タイトルなし"


def get_episode_links(novel_url):
    response = requests.get(novel_url)
    response.raise_for_status()
    body = response.text
    print("小説情報を取得中...")

    ep_pattern = r'"__typename":"Episode","id":"(.*?)","title":"(.*?)"'
    matches = re.findall(ep_pattern, body)

    if not matches:
        print("指定されたページからエピソード情報を取得できませんでした。")
        return []

    base_url_match = re.match(r"(https://kakuyomu.jp/works/\d+)", novel_url)
    if not base_url_match:
        print("小説のURLからベースURLを抽出できませんでした。")
        return []

    base_url = base_url_match.group(1)
    return [(f"{base_url}/episodes/{ep_id}", ep_title) for ep_id, ep_title in matches]


def download_episode(episode_url, episode_title, novel_title, index):
    response = requests.get(episode_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    body = soup.select_one("div.widget-episodeBody").get_text("\n", strip=True)

    folder_num = (index // 999) + 1
    folder_name = f"{folder_num:03d}"
    file_name = f"{index + 1:03d}.txt"

    safe_title = re.sub(r'[\\/*?:"<>|]', '_', novel_title)[:30]
    base_path = os.path.join(DOWNLOAD_DIR_BASE, safe_title, folder_name)

    for lang in ["japanese", "english"]:
        lang_path = os.path.join(base_path, lang)
        os.makedirs(lang_path, exist_ok=True)

    # 日本語保存
    ja_path = os.path.join(base_path, "japanese", file_name)
    with open(ja_path, "w", encoding="utf-8") as f:
        f.write(f"{episode_title}\n\n{body}")

    # 英語翻訳＆保存
    translated_body = translate_text(body)
    en_path = os.path.join(base_path, "english", file_name)
    with open(en_path, "w", encoding="utf-8") as f:
        f.write(f"{episode_title}\n\n{translated_body}")

    if (index + 1) % 300 == 0:
        print(f"{index + 1}話ダウンロード完了。30秒の休憩を取ります...")
        time.sleep(30)


def download_novels(urls, history):
    for novel_url in urls:
        try:
            print(f'\n--- 処理開始: {novel_url} ---')
            novel_title = get_novel_title(novel_url).strip()
            novel_title = re.sub(r'[\\/*?:"<>|]', '', novel_title)

            episode_links = get_episode_links(novel_url)
            download_from = history.get(novel_url, 0)
            new_max = download_from

            for i, (episode_url, episode_title) in enumerate(episode_links):
                if i + 1 <= download_from:
                    continue

                print(f"{i + 1:03d}_{episode_title} downloading & translating...")
                download_episode(episode_url, episode_title, novel_title, i)
                new_max = i + 1

            history[novel_url] = new_max

        except Exception as e:
            print(f"エラー発生: {novel_url} → {e}")
            continue


# ==== メイン処理 ====
if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    url_file_path = os.path.join(script_dir, 'カクヨム.txt')

    with open(url_file_path, 'r', encoding='utf-8') as f:
        urls = [line.strip().rstrip('/') for line in f if line.strip().startswith('http')]

    history = load_history()
    download_novels(urls, history)
    save_history(history)

    subprocess.run([
        'rclone', 'copy', DOWNLOAD_DIR_BASE, 'drive:',
        '--transfers=4', '--checkers=8', '--fast-list'
    ], check=True)
