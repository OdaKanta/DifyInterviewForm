import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder
import io
import datetime
from streamlit_gsheets import GSheetsConnection
import pandas as pd

# --- 1. ユーザー情報の設定 ---
names = ["田中 太郎", "佐藤 花子", "工大 太郎"]
usernames = ["tanaka", "sato", "kodai"]
passwords = ["pass123", "pass456", "password"]

authenticator = stauth.Authenticate(
    {'usernames': {
        usernames[0]: {'name': names[0], 'password': passwords[0]},
        usernames[1]: {'name': names[1], 'password': passwords[1]},
        usernames[2]: {'name': names[2], 'password': passwords[2]}
    }},
    "dify_app_cookie", "signature_key", cookie_expiry_days=30
)

# ログイン画面の表示
authenticator.login('main')

if st.session_state["authentication_status"]:
    username = st.session_state["username"]
    name = st.session_state["name"]
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    conn = st.connection("gsheets", type=GSheetsConnection)

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""

    # --- Dify 送信用の共通設定 ---
    def call_dify_api(query_text):
        DIFY_KEY = st.secrets["DIFY_API_KEY"]
        headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}
        
        # 入力フィールド（material）にファイルを指定する設定
        inputs_data = {
            "material": {
                "transfer_method": "local_file",
                "upload_file_id": "18afae38-1174-4f49-adda-020f12f4c0a7",
                "type": "document"
            }
        }
        
        data = {
            "inputs": inputs_data,
            "query": query_text,
            "response_mode": "streaming",
            "user": username,
            "conversation_id": st.session_state.conversation_id
        }
        return requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=data, stream=True)

    # --- ★追加：ログイン直後の「最初の挨拶」 ---
    if len(st.session_state.messages) == 0:
        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""
            
            # 初回は「こんにちは」などのトリガーを送る
            response = call_dify_api("こんにちは")

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        chunk = json.loads(decoded_line[6:])
                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]
                        
                        # チャットボット形式またはチャットフロー形式から文字を抽出
                        if "answer" in chunk:
                            full_response += chunk["answer"]
                        elif "event" in chunk and chunk["event"] == "text_chunk":
                            full_response += chunk["data"].get("text", "")
                        
                        response_placeholder.markdown(full_response + "▌")

            response_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})
            
            if full_response.strip():
                tts_res = client.audio.speech.create(model="tts-1", voice="alloy", input=full_response)
                st.audio(io.BytesIO(tts_res.content), format="audio/mp3", autoplay=True)

    # --- 過去のメッセージを表示 ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- 入力セクション ---
    audio = mic_recorder(start_prompt="⏺️ 録音開始", stop_prompt="⏹️ 停止", key='recorder')
    input_text = st.chat_input("メッセージを入力...")
    
    current_user_input = None
    if audio:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声を解析中...'):
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_bio)
            current_user_input = transcript.text
    elif input_text:
        current_user_input = input_text

    # --- メイン処理 (Dify送信 & ログ保存) ---
    if current_user_input:
        st.session_state.messages.append({"role": "user", "content": current_user_input})
        with st.chat_message("user"):
            st.markdown(current_user_input)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""

            response = call_dify_api(current_user_input)

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        chunk = json.loads(decoded_line[6:])
                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]
                        
                        if "answer" in chunk:
                            full_response += chunk["answer"]
                        elif "event" in chunk and chunk["event"] == "text_chunk":
                            full_response += chunk["data"].get("text", "")
                            
                        response_placeholder.markdown(full_response + "▌")

            response_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})

            # --- ログ書き込み処理 ---
            try:
                now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
                existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
                new_row = {
                    "date": now, "user_id": username, "user_input": current_user_input, 
                    "ai_response": full_response, "conversation_id": st.session_state.conversation_id
                }
                updated_df = pd.concat([existing_data, pd.DataFrame([new_row])], ignore_index=True)
                conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
            except Exception as e:
                st.error(f"ログ保存エラー: {e}")

            # --- 音声出力 ---
            if full_response.strip():
                with st.spinner('音声を生成中...'):
                    tts_response = client.audio.speech.create(model="tts-1", voice="alloy", input=full_response)
                    st.audio(io.BytesIO(tts_response.content), format="audio/mp3", autoplay=True)
