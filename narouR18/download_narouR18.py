import os
import re
import subprocess
import requests
from bs4 import BeautifulSoup
from g4f.client import Client

BASE_URL = 'https://novel18.syosetu.com'
HISTORY_FILE = '小説家になろうR18ダウンロード経歴.txt'
LOCAL_HISTORY_PATH = f'/tmp/{HISTORY_FILE}'
REMOTE_HISTORY_PATH = f'drive:{HISTORY_FILE}'
COOKIES = {'over18': 'yes'}
client = Client()


def fetch_url(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    return requests.get(url, headers=headers, cookies=COOKIES)


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


# URL一覧の読み込み
script_dir = os.path.dirname(__file__)
url_file_path = os.path.join(script_dir, '小説家になろうR18.txt')
with open(url_file_path, 'r', encoding='utf-8') as f:
    urls = [line.strip().rstrip('/') for line in f if line.strip().startswith('http')]

history = load_history()

for novel_url in urls:
    try:
        print(f'\n--- 処理開始: {novel_url} ---')
        url = novel_url
        sublist = []

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

        for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
            title_text = title_text.replace(char, '')
        title_text = title_text.strip()

        download_from = history.get(novel_url, 0)
        count_since_last_upload = 0

        for i, sub in enumerate(sublist):
            file_num = i + 1
            if file_num <= download_from:
                continue

            sub_title = sub.text.strip()
            link = sub.get('href')
            file_name = f'{file_num:03d}.txt'
            folder_index = ((file_num - 1) // 999) + 1
            folder_name = f'{folder_index:03d}'

            base_path = f'/tmp/narouR18_dl/{title_text}/{folder_name}'
            path_ja = os.path.join(base_path, 'japanese')
            path_en = os.path.join(base_path, 'english')
            os.makedirs(path_ja, exist_ok=True)
            os.makedirs(path_en, exist_ok=True)

            file_path_ja = os.path.join(path_ja, file_name)
            file_path_en = os.path.join(path_en, file_name)

            res = fetch_url(f'{BASE_URL}{link}')
            soup = BeautifulSoup(res.text, 'html.parser')
            sub_body = soup.select_one('.p-novel__body')
            sub_body_text = sub_body.get_text() if sub_body else '[本文が取得できませんでした]'

            with open(file_path_ja, 'w', encoding='UTF-8') as f:
                f.write(f'{sub_title}\n\n{sub_body_text}')

            translated_body = translate_text(sub_body_text)

            with open(file_path_en, 'w', encoding='UTF-8') as f:
                f.write(f'{sub_title}\n\n{translated_body}')

            print(f'{file_name} downloaded in folder {folder_name} ({file_num}/{len(sublist)})')

            # 履歴の即時更新・保存
            history[novel_url] = file_num
            save_history(history)

            # アップロード判定（10話ごと）
            count_since_last_upload += 1
            if count_since_last_upload >= 10:
                print('Uploading 10話分の本文をGoogle Driveへ...')
                subprocess.run(['rclone', 'copy', '/tmp/narouR18_dl', 'drive:', '--transfers=4', '--checkers=8', '--fast-list'], check=True)
                count_since_last_upload = 0

        # 最終残り分のアップロード
        if count_since_last_upload > 0:
            print('Uploading 最終分の本文をGoogle Driveへ...')
            subprocess.run(['rclone', 'copy', '/tmp/narouR18_dl', 'drive:', '--transfers=4', '--checkers=8', '--fast-list'], check=True)

    except Exception as e:
        print(f'エラー発生: {novel_url} → {e}')
        continue
