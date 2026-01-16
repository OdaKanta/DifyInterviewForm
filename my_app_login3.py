import streamlit as st
import requests
import os
import datetime
import pandas as pd
from streamlit_gsheets import GSheetsConnection
import json
import base64

# --- 追加ライブラリ ---
from streamlit_mic_recorder import mic_recorder
from openai import OpenAI

# --- スプレッドシート接続 ---
conn = st.connection("gsheets", type=GSheetsConnection)

# --- 設定 ---
DIFY_API_KEY = st.secrets["DIFY_API_KEY"]
# 【追加】OpenAIクライアントの初期化 (TTS/STT用)
# .streamlit/secrets.toml に OPENAI_API_KEY = "sk-..." を記述してください
openai_client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

BASE_URL = "https://api.dify.ai/v1"
FILE_VARIABLE_KEY = "material"

# サーバー側にある固定ファイルのパス
FIXED_FILE_PATH = "NLP11.pdf"

headers = {
    "Authorization": f"Bearer {DIFY_API_KEY}"
}

# --- ログイン機能 ---
def login():
    """簡易ログイン機能"""
    if "username" not in st.session_state:
        st.session_state.username = None

    if not st.session_state.username:
        with st.form("login_form"):
            st.write("学習を開始するにはID（氏名または学籍番号）を入力してください")
            username_input = st.text_input("ユーザーID")
            submitted = st.form_submit_button("開始")
            if submitted and username_input:
                st.session_state.username = username_input
                st.rerun()
        st.stop()

# --- Dify連携関数群 ---
def upload_local_file_to_dify(file_path, user_id):
    if not os.path.exists(file_path):
        st.error(f"ファイルが見つかりません: {file_path}")
        return None

    url = f"{BASE_URL}/files/upload"
    with open(file_path, "rb") as f:
        files = {
            'file': (os.path.basename(file_path), f, 'application/pdf')
        }
        data = {'user': user_id}
        try:
            response = requests.post(url, headers=headers, files=files, data=data)
            response.raise_for_status()
            return response.json().get('id')
        except Exception as e:
            st.error(f"内部アップロードエラー: {e}")
            return None

def send_chat_message(query, conversation_id, file_id_to_send, user_id):
    url = f"{BASE_URL}/chat-messages"
    inputs = {}
    
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
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"通信エラー: {e}")
        return None

# --- ログ保存機能 ---
def save_log_to_sheet(username, user_input, bot_question, conversation_id):
    try:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
        existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
        
        new_row = pd.DataFrame([{
            "date": now,
            "user_id": username,
            "user_input": user_input,
            "ai_response": bot_question,
            "conversation_id": conversation_id
        }])
        
        if existing_data.empty:
            updated_df = new_row
        else:
            updated_df = pd.concat([existing_data, new_row], ignore_index=True)
            
        conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
    except Exception as e:
        st.error(f"ログ保存エラー: {e}")

# --- 【新規追加】音声処理関数 ---

def transcribe_audio(audio_bytes):
    """OpenAI Whisperを使って音声をテキストに変換"""
    try:
        # OpenAI APIはファイルオブジェクトを必要とするため、一時ファイル等は使わず
        # io.BytesIOに名前をつけて渡すテクニックを使います
        import io
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "input.wav" # 拡張子が重要
        
        transcript = openai_client.audio.transcriptions.create(
            model="whisper-1", 
            file=audio_file,
            language="ja" # 日本語を指定すると精度向上
        )
        return transcript.text
    except Exception as e:
        st.error(f"音声認識エラー: {e}")
        return ""

def text_to_speech_autoplay(text):
    """OpenAI TTSを使ってテキストを音声に変換し、自動再生用HTMLを生成"""
    try:
        response = openai_client.audio.speech.create(
            model="tts-1",
            voice="alloy", # alloy, echo, fable, onyx, nova, shimmer から選択可
            input=text
        )
        
        # 音声データをBase64エンコードしてHTML Audioタグに埋め込む
        audio_bytes = response.content
        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        audio_tag = f'<audio autoplay="true" controls><source src="data:audio/mp3;base64,{audio_base64}" type="audio/mp3"></audio>'
        
        return audio_tag
    except Exception as e:
        st.error(f"音声合成エラー: {e}")
        return None

# ==========================================
# メイン処理
# ==========================================
st.set_page_config(page_title="講義の復習", page_icon="🤖")
st.title("🤖 講義振り返りインタビュアー")

# 1. ログイン & セッション初期化
login()
current_user = st.session_state.username
st.sidebar.write(f"ログイン中: {current_user}")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = ""
if "current_file_id" not in st.session_state:
    st.session_state.current_file_id = None
if "last_bot_message" not in st.session_state:
    st.session_state.last_bot_message = ""
# 【追加】音声再生用のHTMLを保持する変数
if "audio_html" not in st.session_state:
    st.session_state.audio_html = None

# --- 緊急リセットボタン ---
if st.sidebar.button("⚠️ 会話をリセット"):
    st.session_state.conversation_id = ""
    st.session_state.messages = []
    st.session_state.current_file_id = None
    st.session_state.last_bot_message = ""
    st.session_state.audio_html = None
    st.rerun()

# 2. 自動初期化（ファイルアップロード & 初回質問）
if not st.session_state.conversation_id:
    with st.spinner("インタビュアーを準備中..."):
        if not st.session_state.current_file_id:
            file_id = upload_local_file_to_dify(FIXED_FILE_PATH, current_user)
            if file_id:
                st.session_state.current_file_id = file_id
            else:
                st.error("ファイルのアップロードに失敗しました。")
                st.stop()
        
        initial_res = send_chat_message(
            query="授業内容について学んだことを教えてください。", 
            conversation_id="",
            file_id_to_send=st.session_state.current_file_id,
            user_id=current_user
        )
        
        if initial_res:
            st.session_state.conversation_id = initial_res.get('conversation_id')
            welcome_msg = initial_res.get('answer', '')
            st.session_state.messages.append({"role": "assistant", "content": welcome_msg})
            st.session_state.last_bot_message = welcome_msg
            
            # 初回メッセージも音声再生する場合
            audio_tag = text_to_speech_autoplay(welcome_msg)
            st.session_state.audio_html = audio_tag
            
            st.rerun()

# 3. チャット履歴の表示
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# 【追加】音声自動再生（最新のAI応答がある場合、画面上部や末尾で再生される）
#  st.empty() を使って、再生が終わったら消す制御も可能ですが、履歴に残らないようにここで表示
if st.session_state.audio_html:
    st.markdown(st.session_state.audio_html, unsafe_allow_html=True)
    # 一度再生用に表示したら、リロード時に再再生されないようにクリアしたいが、
    # Streamlitのライフサイクル上、ここをNoneにすると即座に消えて再生されないため、
    # 新しい入力があったタイミングでクリアされる運用にします。

# 4. 入力エリア（音声 OR テキスト）
st.divider()
col1, col2 = st.columns([1, 4])

# 音声入力ボタン
with col1:
    st.write("音声入力:")
    audio = mic_recorder(
        start_prompt="録音開始",
        stop_prompt="録音終了",
        key='recorder',
        format="wav" # Whisperはwav対応
    )

# テキスト入力ボックス
user_input_text = st.chat_input("テキストで入力...")

# 5. 入力処理ロジック
final_prompt = None

# A. 音声入力があった場合
if audio:
    # mic_recorderは録音完了時にバイトデータを返します
    with st.spinner("音声認識中..."):
        transcribed_text = transcribe_audio(audio['bytes'])
        if transcribed_text:
            final_prompt = transcribed_text
            # 既存の音声プレーヤーを消去（自分の声が認識されたら前の音声は不要）
            st.session_state.audio_html = None

# B. テキスト入力があった場合
elif user_input_text:
    final_prompt = user_input_text
    st.session_state.audio_html = None

# C. 入力が確定した場合の送信処理
if final_prompt:
    # ユーザー発言を表示
    st.session_state.messages.append({"role": "user", "content": final_prompt})
    with st.chat_message("user"):
        st.write(final_prompt)

    with st.spinner("AIが思考中..."):
        # Difyへ送信
        response = send_chat_message(
            query=final_prompt,
            conversation_id=st.session_state.conversation_id,
            file_id_to_send=st.session_state.current_file_id,
            user_id=current_user
        )
        
        if response:
            answer_text = response.get('answer', '')
            
            # メッセージ履歴に追加
            st.session_state.messages.append({"role": "assistant", "content": answer_text})
            
            # ログ保存
            save_log_to_sheet(
                username=current_user,
                user_input=final_prompt,
                bot_question=st.session_state.last_bot_message, 
                conversation_id=st.session_state.conversation_id
            )
            
            # 直前の質問を更新
            st.session_state.last_bot_message = answer_text
            
            # 【追加】回答テキストを音声に変換してセット
            audio_tag = text_to_speech_autoplay(answer_text)
            st.session_state.audio_html = audio_tag

            # 画面更新してAIの回答と音声プレーヤーを表示
            st.rerun()
