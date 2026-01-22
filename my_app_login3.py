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

# --- スプレッドシート接続 ---
conn = st.connection("gsheets", type=GSheetsConnection)

# --- 設定 ---
DIFY_API_KEY = st.secrets["DIFY_API_KEY"]
openai_client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

BASE_URL = "https://api.dify.ai/v1"
FILE_VARIABLE_KEY = "material"
FIXED_FILE_PATH = "geology01.pdf"

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
            username_input = st.text_input("ユーザーID")
            # パスワード入力（type="password"で文字を隠す）
            password_input = st.text_input("パスワード", type="password")
            
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

def transcribe_audio(audio_bytes):
    try:
        import io
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "input.wav"
        
        # 認識させたい専門用語
        vocab_prompt = ("東京特許許可局")

        transcript = openai_client.audio.transcriptions.create(
            model="whisper-1", 
            file=audio_file, 
            language="ja",
            prompt=vocab_prompt,
            temperature=0.0
        )
        return transcript.text
    except Exception as e:
        st.error(f"音声認識エラー: {e}")
        return ""

def correct_transcript(text):
    """Whisperの誤認識をLLMで直す関数"""
    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4o-mini", # 高速・安価なモデル
            messages=[
                {"role": "system", "content": "あなたは優秀な校正者です。以下の文章はあるテキストの音声認識結果であり、日本語として不自然な文字や言葉、表現である可能性があります。文脈を考慮して、明らかな誤り（同音異義語など）を修正してください。元の意味は大きく変えてはいけません、余計な返事をせず、修正後のテキストのみを出力してください。"},
                {"role": "user", "content": text}
            ],
            temperature=0
        )
        return completion.choices[0].message.content
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
st.set_page_config(page_title="講義の復習", page_icon="🤖")
st.title("🤖 講義振り返りインタビュアー")

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
if "last_bot_message" not in st.session_state:
    st.session_state.last_bot_message = ""
if "audio_html" not in st.session_state:
    st.session_state.audio_html = None
if "prev_audio_bytes" not in st.session_state:
    st.session_state.prev_audio_bytes = None

# 【追加】テキスト入力処理用の一時変数
if "temp_user_input" not in st.session_state:
    st.session_state.temp_user_input = ""
if "input_to_process" not in st.session_state:
    st.session_state.input_to_process = None

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
                file_id = upload_local_file_to_dify(FIXED_FILE_PATH, current_user)
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
chat_container = st.container(height=500)

with chat_container:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
    if st.session_state.audio_html:
        st.markdown(st.session_state.audio_html, unsafe_allow_html=True)

# 5. 入力エリア & 6. 入力処理ロジック（統合・順序修正版）
st.divider()

def submit_text():
    st.session_state.input_to_process = st.session_state.temp_user_input
    st.session_state.temp_user_input = "" 

# レイアウト定義（見た目は 左:入力、右:マイク）
col_input, col_mic = st.columns([6, 1])

# 【重要】実行順序の変更
# 見た目は「右」ですが、コード上は「マイク」を先に処理します。
# こうすることで、テキスト入力欄が描画される「前」に音声を文字に変換してセットできます。

# --- A. マイク入力と音声処理（先出し） ---
with col_mic:
    audio = mic_recorder(
        start_prompt="🎤", 
        stop_prompt="⏹️", 
        key='recorder', 
        format="wav"
    )

# 音声データがある場合、すぐに処理して session_state を更新する
if audio:
    if audio['bytes'] != st.session_state.prev_audio_bytes:
        st.session_state.prev_audio_bytes = audio['bytes']
        
        with st.spinner("音声認識中..."):
            transcribed_text = transcribe_audio(audio['bytes'])
            if transcribed_text:
                corrected_text = correct_transcript(transcribed_text)
                
                # 【ここが修正のキモ】
                # まだテキスト入力欄は描画されていないので、ここで値をセットしてもエラーになりません！
                st.session_state.temp_user_input = corrected_text
                
                # 前のボットの音声を停止
                st.session_state.audio_html = None
                
                # ここで rerun する必要はありません。
                # このまま下のコードに進めば、自然に新しい値が入った状態で入力欄が表示されます。

# --- B. テキスト入力エリア（後出し） ---
with col_input:
    # ここで初めて入力欄が描画されます。
    # 上の処理で temp_user_input に値が入っていれば、それが初期値として表示されます。
    st.text_input(
        label="メッセージ入力",
        key="temp_user_input",
        placeholder="テキストを入力してEnter...",
        label_visibility="collapsed",
        on_change=submit_text
    )

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
            user_id=current_user
        )
        
        if response:
            st.session_state.conversation_id = response.get('conversation_id')
            answer_text = response.get('answer', '')
            st.session_state.messages.append({"role": "assistant", "content": answer_text})
            
            save_log_to_sheet(
                username=current_user,
                user_input=final_prompt,
                bot_question=st.session_state.last_bot_message, 
                conversation_id=st.session_state.conversation_id
            )
            
            st.session_state.last_bot_message = answer_text
            st.session_state.audio_html = text_to_speech_autoplay(answer_text)

            st.rerun()
