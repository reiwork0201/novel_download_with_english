import os
import requests
from bs4 import BeautifulSoup
import re
import subprocess
from openai import OpenAI

# OpenAI API クライアント設定（ChatAnywhere）
client = OpenAI(
    api_key="sk-AiXqahIuLOoxCDF16CnXTRsI9xSZy7D7KwbUaTqTnNzcSNju",
    base_url="https://api.chatanywhere.tech/v1"
)

BASE_URL = 'https://ncode.syosetu.com'
HISTORY_FILE = '小説家になろうダウンロード経歴.txt'
LOCAL_HISTORY_PATH = f'/tmp/{HISTORY_FILE}'
REMOTE_HISTORY_PATH = f'drive:{HISTORY_FILE}'

def fetch_url(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    return requests.get(url, headers=headers)

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
                    history[url.rstrip('/')] = int(last)
    return history

def save_history(history):
    with open(LOCAL_HISTORY_PATH, 'w', encoding='utf-8') as f:
        for url, last in history.items():
            f.write(f'{url}  |  {last}\n')
    subprocess.run(['rclone', 'copyto', LOCAL_HISTORY_PATH, REMOTE_HISTORY_PATH], check=True)

def translate_text(japanese_text):
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional translator specializing in Japanese fantasy novels. "
                        "Translate the following passage into natural, expressive English that preserves the original tone, atmosphere, and character voices. "
                        "Do NOT add any explanations, comments, headers, or footnotes. "
                        "Output ONLY the translated text."
                    )
                },
                {
                    "role": "user",
                    "content": japanese_text
                }
            ],
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[Translation failed: {e}]"

# URL一覧の読み込み
script_dir = os.path.dirname(__file__)
url_file_path = os.path.join(script_dir, '小説家になろう.txt')
with open(url_file_path, 'r', encoding='utf-8') as f:
    urls = [line.strip().rstrip('/') for line in f if line.strip().startswith('http')]

history = load_history()

for novel_url in urls:
    try:
        print(f'\n--- 処理開始: {novel_url} ---')
        url = novel_url
        sublist = []

        # ページ分割対応
        while True:
            res = fetch_url(url)
            soup = BeautifulSoup(res.text, 'html.parser')
            title_text = soup.find('title').get_text()
            sublist += soup.select('.p-eplist__sublist .p-eplist__subtitle')
            next = soup.select_one('.c-pager__item--next')
            if next and next.get('href'):
                url = f'{BASE_URL}{next.get("href")}'
            else:
                break

        # ファイル名として使えない文字除去
        for char in '<>:"/\\|?*':
            title_text = title_text.replace(char, '')
        title_text = title_text.strip()

        download_from = history.get(novel_url, 0)
        base_path = f'/tmp/narou_dl/{title_text}'

        sub_len = len(sublist)
        new_max = download_from

        for i, sub in enumerate(sublist):
            if i + 1 <= download_from:
                continue

            sub_title = sub.text.strip()
            link = sub.get('href')
            file_name = f'{(i % 999) + 1:03d}.txt'
            folder_num = (i // 999) + 1
            folder_name = f'{folder_num:03d}'
            jp_path = os.path.join(base_path, folder_name, 'japanese')
            en_path = os.path.join(base_path, folder_name, 'english')
            os.makedirs(jp_path, exist_ok=True)
            os.makedirs(en_path, exist_ok=True)

            file_path_jp = os.path.join(jp_path, file_name)
            file_path_en = os.path.join(en_path, file_name)

            res = fetch_url(f'{BASE_URL}{link}')
            soup = BeautifulSoup(res.text, 'html.parser')
            sub_body = soup.select_one('.p-novel__body')
            sub_body_text = sub_body.get_text().strip() if sub_body else '[本文が取得できませんでした]'
            full_text = f'{sub_title}\n\n{sub_body_text}'

            # 保存（日本語）
            with open(file_path_jp, 'w', encoding='utf-8') as f:
                f.write(full_text)

            # 翻訳して保存（英語）
            translated_body = translate_text(sub_body_text)
            translated_text = f'{sub_title}\n\n{translated_body}'
            with open(file_path_en, 'w', encoding='utf-8') as f:
                f.write(translated_text)

            print(f'{file_name} saved in {folder_name} (japanese & english) ({i+1}/{sub_len})')
            new_max = i + 1

        history[novel_url] = new_max

    except Exception as e:
        print(f'エラー発生: {novel_url} → {e}')
        continue

save_history(history)

# Google Driveにアップロード
subprocess.run(['rclone', 'copy', '/tmp/narou_dl', 'drive:', '--transfers=4', '--checkers=8', '--fast-list'], check=True)
