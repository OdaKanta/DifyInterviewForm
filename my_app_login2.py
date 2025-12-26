import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder
import io
import yaml
from streamlit_gsheets import GSheetsConnection
import datetime
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
    "dify_app_cookie", 
    "signature_key",   
    cookie_expiry_days=30
)

authenticator.login('main')

if st.session_state["authentication_status"]:
    username = st.session_state["username"]
    name = st.session_state["name"]
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    conn = st.connection("gsheets", type=GSheetsConnection)

    with st.sidebar:
        st.write(f"ようこそ、{name} さん")
        authenticator.logout('ログアウト', 'sidebar')

    st.title("音声対応AIアシスタント 0")

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""

    # --- 入力 UI ---
    st.write("話しかけてください：")
    audio = mic_recorder(start_prompt="⏺️ 録音開始", stop_prompt="⏹️ 停止", key='recorder')
    user_input = None

    if audio:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声を解析中...'):
            try:
                transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_bio)
                user_input = transcript.text
            except Exception as e:
                st.error(f"音声認識エラー: {e}")
    
    chat_input = st.chat_input("またはメッセージを入力...")
    if chat_input:
        user_input = chat_input

    # 履歴の表示
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- メイン処理 ---
    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""

            DIFY_KEY = st.secrets["DIFY_API_KEY"]
            headers = {
                "Authorization": f"Bearer {DIFY_KEY}", 
                "Content-Type": "application/json"
            }
            
            # --- エラー回避のためのリクエスト構成 ---
            data = {
                "inputs": {}, 
                "query": user_input,
                "response_mode": "streaming",
                "user": username,
                "files": [] # 400 Bad Request (file mapping) 対策
            }
            
            if st.session_state.conversation_id:
                data["conversation_id"] = st.session_state.conversation_id

            try:
                response = requests.post(
                    "https://api.dify.ai/v1/chat-messages", 
                    headers=headers, 
                    json=data, 
                    stream=True,
                    timeout=60
                )
                
                if response.status_code != 200:
                    st.error(f"Dify API Error {response.status_code}: {response.text}")
                else:
                    for line in response.iter_lines():
                        if not line:
                            continue
                        
                        decoded_line = line.decode('utf-8')
                        if not decoded_line.startswith('data: '):
                            continue
                        
                        try:
                            # 'data: ' の後の文字列を抽出
                            json_str = decoded_line[6:]
                            chunk = json.loads(json_str)
                            event = chunk.get("event")

                            if "conversation_id" in chunk:
                                st.session_state.conversation_id = chunk["conversation_id"]

                            # Chatflow / Chatbot 両方のイベントに対応
                            if event in ["message", "text_chunk"]:
                                content = ""
                                if event == "message":
                                    content = chunk.get("answer", "")
                                elif event == "text_chunk":
                                    content = chunk.get("data", {}).get("text", "")
                                
                                full_response += content
                                response_placeholder.markdown(full_response + "▌")
                            
                            elif event == "error":
                                st.error(f"Dify内部エラー: {chunk.get('message')}")

                        except json.JSONDecodeError:
                            continue

                    response_placeholder.markdown(full_response)
                    if full_response:
                        st.session_state.messages.append({"role": "assistant", "content": full_response})

            except Exception as e:
                st.error(f"接続エラー: {e}")

            # --- ログ保存と音声出力 ---
            if full_response:
                # ログ保存 (Google Sheets)
                try:
                    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
                    existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
                    new_row_df = pd.DataFrame([{
                        "date": now, "user_id": username, "user_input": user_input,
                        "ai_response": full_response, "conversation_id": st.session_state.conversation_id
                    }])
                    updated_df = pd.concat([existing_data, new_row_df], ignore_index=True)
                    conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
                except Exception as e:
                    print(f"Log Error: {e}")

                # 音声生成
                with st.spinner('音声を生成中...'):
                    try:
                        tts_res = client.audio.speech.create(
                            model="tts-1", 
                            voice="alloy", 
                            input=full_response[:4096]
                        )
                        st.audio(io.BytesIO(tts_res.content), format="audio/mp3", autoplay=True)
                    except Exception as e:
                        st.error(f"音声生成エラー: {e}")
            else:
                if response.status_code == 200:
                    st.warning("AIからの回答が空でした。Difyの出力設定を確認してください。")

elif st.session_state["authentication_status"] is False:
    st.error('ユーザー名またはパスワードが間違っています')
elif st.session_state["authentication_status"] is None:
    st.warning('ユーザー名とパスワードを入力してください')
