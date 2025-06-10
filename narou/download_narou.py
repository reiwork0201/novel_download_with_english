import os
import requests
from bs4 import BeautifulSoup
import re
import subprocess
from g4f.client import Client

BASE_URL = 'https://ncode.syosetu.com'
HISTORY_FILE = '小説家になろうダウンロード経歴.txt'
LOCAL_HISTORY_PATH = f'/tmp/{HISTORY_FILE}'
REMOTE_HISTORY_PATH = f'drive:{HISTORY_FILE}'

client = Client()

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
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": (
                    "You are a professional translator specializing in Japanese fantasy novels. "
        "Translate the following passage into natural, expressive English that preserves the original tone, atmosphere, and character voices. "
        "Remove any forewords or afterwords such as author notes, promotional content, or update logs. Output only the translated body."
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

        for char in '<>:"/\\|?*':
            title_text = title_text.replace(char, '')
        title_text = title_text.strip()

        download_from = history.get(novel_url, 0)
        base_path = f'/tmp/narou_dl/{title_text}'
        sub_len = len(sublist)
        new_max = download_from
        download_count = 0

        for i, sub in enumerate(sublist):
            file_index = i + 1
            if file_index <= download_from:
                continue

            sub_title = sub.text.strip()
            link = sub.get('href')
            folder_num = ((file_index - 1) // 999) + 1
            folder_name = f'{folder_num:03d}'
            file_name = f'{file_index:03d}.txt'

            jp_path = os.path.join(base_path, folder_name, 'japanese')
            en_path = os.path.join(base_path, folder_name, 'english')
            os.makedirs(jp_path, exist_ok=True)
            os.makedirs(en_path, exist_ok=True)

            file_path_jp = os.path.join(jp_path, file_name)
            file_path_en = os.path.join(en_path, file_name)

            # 本文取得＆保存
            res = fetch_url(f'{BASE_URL}{link}')
            soup = BeautifulSoup(res.text, 'html.parser')
            sub_body = soup.select_one('.p-novel__body')
            sub_body_text = sub_body.get_text().strip() if sub_body else '[本文が取得できませんでした]'
            full_text_jp = f'{sub_title}\n\n{sub_body_text}'

            with open(file_path_jp, 'w', encoding='utf-8') as f:
                f.write(full_text_jp)

            translated_body = translate_text(sub_body_text)
            full_text_en = f'{sub_title}\n\n{translated_body}'
            with open(file_path_en, 'w', encoding='utf-8') as f:
                f.write(full_text_en)

            print(f'{file_name} saved in {folder_name} (japanese & english) ({file_index}/{sub_len})')

            # ✅ 各話ごとに履歴保存
            new_max = file_index
            history[novel_url] = new_max
            save_history(history)

            download_count += 1

            # ✅ 10話ごとにアップロード
            if download_count % 10 == 0:
                print(f'\n--- 10話ごとにDriveへアップロード中 ({download_count}話目) ---')
                subprocess.run([
                    'rclone', 'copy',
                    base_path, f'drive:{title_text}',
                    '--transfers=4', '--checkers=8', '--fast-list'
                ], check=True)

        # ✅ 残り話（10未満）をアップロード
        if download_count % 10 != 0:
            print(f'\n--- 最終アップロード: 残りの話をDriveにアップロード中 ({download_count}話) ---')
            subprocess.run([
                'rclone', 'copy',
                base_path, f'drive:{title_text}',
                '--transfers=4', '--checkers=8', '--fast-list'
            ], check=True)

    except Exception as e:
        print(f'エラー発生: {novel_url} → {e}')
        continue
