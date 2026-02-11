import streamlit as st
import requests
import os
import datetime
import pandas as pd
from streamlit_gsheets import GSheetsConnection
import json
import base64
import io

# --- 追加ライブラリ ---
from streamlit_mic_recorder import mic_recorder
from openai import OpenAI
import gspread

# --- スプレッドシート接続 ---
conn = st.connection("gsheets", type=GSheetsConnection)

# --- 設定 ---
DIFY_API_KEY = st.secrets["DIFY_API_KEY"]
openai_client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

BASE_URL = "https://api.dify.ai/v1"
FILE_VARIABLE_KEY = "material"
MATERIALS = {
    "地学基礎　第1講": {"pdf": "geology01.pdf", "keywords": "keywords01.txt"},
    "地学基礎　第3講": {"pdf": "geology03.pdf", "keywords": "keywords03.txt"}
}

headers = {
    "Authorization": f"Bearer {DIFY_API_KEY}"
}

# --- ログイン機能（パスワード認証版） ---
def login():
    """IDとパスワードによる認証機能"""
    if "username" not in st.session_state:
        st.session_state.username = None

    if not st.session_state.username:
        with st.form("login_form"):
            st.write("学習を開始するにはログインしてください")
            
            # ユーザーID入力
            username_input = st.text_input("ユーザーID", key="login_user_id")
            # パスワード入力（type="password"で文字を隠す）
            password_input = st.text_input("パスワード", type="password", key="login_password")
            
            submitted = st.form_submit_button("ログイン")
            
            if submitted:
                # 1. IDが secrets に存在するか？
                if username_input in st.secrets["passwords"]:
                    # 2. パスワードが一致するか？
                    correct_password = st.secrets["passwords"][username_input]
                    if password_input == correct_password:
                        st.session_state.username = username_input
                        st.success("ログイン成功！")
                        st.rerun()
                    else:
                        st.error("パスワードが間違っています。")
                else:
                    st.error("ユーザーIDが見つかりません。")
        st.stop()

# --- Dify連携関数群 ---
def upload_local_file_to_dify(file_path, user_id):
    if not os.path.exists(file_path):
        st.error(f"ファイルが見つかりません: {file_path}")
        return None
    url = f"{BASE_URL}/files/upload"
    with open(file_path, "rb") as f:
        files = {'file': (os.path.basename(file_path), f, 'application/pdf')}
        data = {'user': user_id}
        try:
            response = requests.post(url, headers=headers, files=files, data=data)
            response.raise_for_status()
            return response.json().get('id')
        except Exception as e:
            st.error(f"内部アップロードエラー: {e}")
            return None

def send_chat_message(query, conversation_id, file_id_to_send, user_id, material_name):
    url = f"{BASE_URL}/chat-messages"
    inputs = {"material_name": material_name}
    if file_id_to_send:
        inputs[FILE_VARIABLE_KEY] = {
            "type": "document", 
            "transfer_method": "local_file",
            "upload_file_id": file_id_to_send
        }
    payload = {
        "inputs": inputs,
        "query": query,
        "response_mode": "blocking",
        "conversation_id": conversation_id,
        "user": user_id,
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 400:
            # 400エラーの時はDifyからの詳細メッセージを表示
            st.error(f"Difyエラー詳細: {response.text}") 
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"通信エラー: {e}")
        return None

# --- ログ保存機能 ---
def save_log_to_sheet(session, user, material, system_question, user_answer):
    try:
        created_date = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
        new_row = [session, user, material, system_question, user_answer, created_date]
        
        # Secretsからサービスアカウント情報を取得して直接認証
        # st.secrets["connections"]["gsheets"] の構造に合わせて指定してください
        creds_dict = st.secrets["connections"]["gsheets"]
        gc = gspread.service_account_from_dict(creds_dict)
        
        # スプレッドシートを開いて1行追記
        sh = gc.open_by_url(st.secrets["spreadsheet_url"])
        ws = sh.get_worksheet(0)
        ws.append_row(new_row)
        
    except Exception as e:
        st.error(f"ログ保存エラー (追記失敗): {e}")

def transcribe_audio(audio_bytes, keyword_file):
    try:
        # 1. キーワードファイルの読み込みと整形
        vocab_prompt = ""
        if os.path.exists(keyword_file):
            with open(keyword_file, "r", encoding="utf-8") as f:
                content = f.read()
                
            all_keywords = []
            # 行ごとに分割（改行区切り対応）
            for line in content.splitlines():
                # 各行をさらにカンマで分割（カンマ区切り対応）
                parts = line.split(',')
                for p in parts:
                    clean_word = p.strip()
                    if clean_word:
                        all_keywords.append(clean_word)
            
            # 重複を除去してカンマ区切りの文字列にする
            unique_keywords = list(dict.fromkeys(all_keywords))
            vocab_prompt = ",".join(unique_keywords)
        
        # 2. 音声認識の実行
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "input.wav"
        
        transcript = openai_client.audio.transcriptions.create(
            model="whisper-1", 
            file=audio_file, 
            language="ja",
            prompt=vocab_prompt, # 指示文なし。単語の羅列を直接渡すのが正解。
            temperature=0.0
        )
        return transcript.text
    except Exception as e:
        st.error(f"音声認識エラー: {e}")
        return ""

def correct_transcript(text, keyword_file):
    """Whisperの誤認識をLLMで直す関数"""
    try:
        # 1. キーワードファイルの読み込みと整形
        keywords_str = ""
        if os.path.exists(keyword_file):
            with open(keyword_file, "r", encoding="utf-8") as f:
                content = f.read()
                
            all_keywords = []
            # 行ごとに分割（改行区切り対応）
            for line in content.splitlines():
                # 各行をさらにカンマで分割（カンマ区切り対応）
                parts = line.split(',')
                for p in parts:
                    clean_word = p.strip()
                    if clean_word:
                        all_keywords.append(clean_word)
            
            # 重複を除去してカンマ区切りの文字列にする
            unique_keywords = list(dict.fromkeys(all_keywords))
            keywords_str = ",".join(unique_keywords)

        prompt = f"""
        生徒が中学理科の授業について振り返った際の録音を文字起こししましたが、認識精度の限界により誤字脱字があるかもしれないので、修正してください。

        ■ 修正の指示
        - 元の文章の意図は保持する（学習者による間違った説明はあえて修正せずそのまま）。
        - 誤字脱字や同音異義語の変換ミスを修正し、不要なフィラー（えー、あのー等）や無意味な文章を削除する。
        - 出力は修正後の文章のみ出力し、余計な文章を勝手に付け加えてはならない。
        - 修正箇所が見つからないほど短い入力の場合でも、文句を言わずにそのまま出力せよ。
        
        ■ 原文（いかに短くても、どうみてもまともな説明でなくても、文句を言ってはならない）
        {text}

        {f"■ 授業内容に含まれる重要語句（この単語への誤変換が疑われる場合に参考にせよ）: {keywords_str}" if keywords_str else ""}
        """
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini", # 高速・安価なモデル
            messages=[
                {"role": "system", "content": "あなたは優秀な校正者です。音声書き起こしにみられる誤字脱字などを修正してください。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0
        )
        return response.choices[0].message.content.strip()
    except:
        return text

def text_to_speech_autoplay(text):
    try:
        response = openai_client.audio.speech.create(
            model="tts-1-hd", voice="nova", input=text
        )
        audio_bytes = response.content
        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        audio_tag = f'<audio autoplay="true" style="display:none"><source src="data:audio/mp3;base64,{audio_base64}" type="audio/mp3"></audio>'
        return audio_tag
    except Exception as e:
        st.error(f"音声合成エラー: {e}")
        return None

# ==========================================
# メイン処理
# ==========================================
st.set_page_config(page_title="講義復習支援チャットボット", page_icon="🤖")
st.title("講義復習支援チャットボット")

login()
current_user = st.session_state.username
st.sidebar.write(f"ログイン中: {current_user}")

# セッション変数
if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = ""
if "current_file_id" not in st.session_state:
    st.session_state.current_file_id = None
if "selected_material" not in st.session_state:
    st.session_state.selected_material = None
if "last_bot_message" not in st.session_state:
    st.session_state.last_bot_message = ""
if "audio_html" not in st.session_state:
    st.session_state.audio_html = None
if "prev_audio_bytes" not in st.session_state:
    st.session_state.prev_audio_bytes = None
if "temp_user_input" not in st.session_state:
    st.session_state.temp_user_input = ""
if "input_to_process" not in st.session_state:
    st.session_state.input_to_process = None
if "is_completed" not in st.session_state:
    st.session_state.is_completed = False

# 1. 講義資料の選択インターフェース
if not st.session_state.selected_material:
    st.subheader("📚 学習する講義資料を選択してください")
    selected = st.radio(
        "講義リスト",
        options=list(MATERIALS.keys()),
        index=None
    )
    
    if st.button("学習を開始する"):
        if selected:
            st.session_state.selected_material = selected
            st.rerun()
        else:
            st.warning("講義資料を選択してください。")
    st.stop() # 選択されるまで下の処理（チャット）に進まない

material_info = MATERIALS[st.session_state.selected_material]
target_material_path = material_info["pdf"]
target_keyword_path = material_info["keywords"]

# --- 緊急リセット ---
if st.sidebar.button("⚠️ 会話をリセット"):
    for key in list(st.session_state.keys()):
        if key not in ["username"]: # ログイン情報は残す
            del st.session_state[key]
    st.rerun()

# 3. 自動初期化（修正版：APIを叩かず、静的に開始する）
if not st.session_state.conversation_id:
    # まだメッセージ履歴がない場合のみ実行
    if not st.session_state.messages:
        with st.spinner("インタビュアーを準備中..."):
            
            # 1. ファイルアップロードだけは済ませておく（ID確保）
            if not st.session_state.current_file_id:
                file_id = upload_local_file_to_dify(target_material_path, current_user)
                if file_id:
                    st.session_state.current_file_id = file_id
                else:
                    st.error("ファイルのアップロードに失敗しました。")
                    st.stop()
            
            # 2. Difyには何も送らず、ここで勝手に第一声を表示する
            static_first_msg = "授業内容について学んだことを教えてください。"
            
            # 画面表示用リストに追加
            st.session_state.messages.append({"role": "assistant", "content": static_first_msg})
            
            # ログ保存用（次のターンのため）
            st.session_state.last_bot_message = static_first_msg
            
            # 音声生成（ここだけはOpenAI APIを叩きますが、Difyは叩きません）
            st.session_state.audio_html = text_to_speech_autoplay(static_first_msg)
            
            # 画面更新して表示
            st.rerun()

# 4. チャット履歴の表示
chat_container = st.container(height=400)

with chat_container:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
    if st.session_state.audio_html:
        st.markdown(st.session_state.audio_html, unsafe_allow_html=True)

if st.session_state.is_completed:
    st.success("🎉 全ての学習項目を確認しました。お疲れ様でした！")
    st.balloons() # お祝いの演出

# 5. 入力エリア & 6. 入力処理ロジック（統合・順序修正版）
def submit_text():
    if st.session_state[input_key]:
        st.session_state.input_to_process = st.session_state[input_key] 

# セッション変数初期化（既存の場所に追加）
if "input_method" not in st.session_state:
    st.session_state.input_method = "text"

# --- 5. 入力エリア ---
col_input, col_send, col_mic = st.columns([5, 1, 1])
input_key = f"chat_input_text_{current_user}"
display_value = st.session_state.temp_user_input

# if st.session_state.temp_user_input:
#     st.session_state[input_key] = st.session_state.temp_user_input
#     st.session_state.input_method = "voice"
#     st.session_state.temp_user_input = ""

if display_value:
    st.session_state.input_method = "voice"

with col_input:
    st.text_input(
        label="メッセージ入力",
        value=display_value,
        key=input_key,
        placeholder="テキストを入力してEnter...",
        label_visibility="collapsed",
        on_change=submit_text
    )
if display_value:
    st.session_state.temp_user_input = ""

with col_send:
    # 修正：temp_user_input ではなく input_key (現在の入力内容) を参照
    if st.button("送信", use_container_width=True):
        st.session_state.input_to_process = st.session_state[input_key]
        # rerunすると現在の入力方法(text or voice)を維持したまま送信処理へ進む
        st.rerun()

# --- A. マイク入力と音声処理（先出し） ---
with col_mic:
    audio = mic_recorder(
        start_prompt="🎤", 
        stop_prompt="🟥", 
        key='recorder', 
        format="wav"
    )

# 音声データがある場合、すぐに処理して session_state を更新する
if audio:
    if audio['bytes'] != st.session_state.prev_audio_bytes:
        st.session_state.prev_audio_bytes = audio['bytes']
        with st.spinner("音声処理中..."): # 文言を短く
            transcribed_text = transcribe_audio(audio['bytes'], target_keyword_path)
            if transcribed_text:
                # miniモデルで高速校正
                st.session_state.temp_user_input = correct_transcript(transcribed_text, target_keyword_path)
                st.session_state.input_method = "voice"
                st.rerun() # 文字が入った状態で即座に再描画

# --- C. 送信処理（Enterが押された後の処理） ---
# コールバック(submit_text)によって input_to_process に値が入っていたら実行
final_prompt = None

if st.session_state.input_to_process:
    final_prompt = st.session_state.input_to_process
    st.session_state.input_to_process = None
    st.session_state.audio_html = None

# 送信実行
if final_prompt:
    st.session_state.messages.append({"role": "user", "content": final_prompt})
    
    with chat_container:
        with st.chat_message("user"):
            st.write(final_prompt)

    with st.spinner("思考中..."):
        response = send_chat_message(
            query=final_prompt,
            conversation_id=st.session_state.conversation_id,
            file_id_to_send=st.session_state.current_file_id,
            user_id=current_user,
            material_name=st.session_state.selected_material
        )
        
        if response:
            st.session_state.conversation_id = response.get('conversation_id')
            answer_text = response.get('answer', '')
            is_finished = response.get('metadata', {}).get('workflow_outputs', {}).get('is_finished', False)
            if is_finished:
                st.session_state.is_completed = True
            st.session_state.messages.append({"role": "assistant", "content": answer_text})
            
            save_log_to_sheet(
                session=st.session_state.conversation_id,
                user=current_user,
                material=st.session_state.selected_material,
                system_question=st.session_state.last_bot_message,
                user_answer=final_prompt
            )
            
            st.session_state.last_bot_message = answer_text
            st.session_state.audio_html = text_to_speech_autoplay(answer_text)
            st.session_state.temp_user_input = ""

            st.rerun()
