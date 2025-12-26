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

    st.title("音声対応AIアシスタント")

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""

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

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

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
            
            # --- 【重要】Chatflow 向けのリクエスト構成 ---
            # 多くのChatflowでは inputs に変数を渡す必要があります。
            # もしDify側で開始ノードの入力変数を 'sys_query' などにしている場合は
            # "inputs": {"sys_query": user_input} のように書き換えてください。
            data = {
                "inputs": {}, 
                "query": user_input, # Chatbot APIとして動作させる場合
                "response_mode": "streaming",
                "user": username
            }
            
            # 会話の継続性を維持
            if st.session_state.conversation_id:
                data["conversation_id"] = st.session_state.conversation_id

            try:
                # タイムアウトを設定してリクエスト
                response = requests.post(
                    "https://api.dify.ai/v1/chat-messages", 
                    headers=headers, 
                    json=data, 
                    stream=True,
                    timeout=60
                )
                
                # ここで 400 エラーの内容を詳細に表示させる
                if response.status_code != 200:
                    st.error(f"Dify API Error {response.status_code}: {response.text}")
                else:
                    for line in response.iter_lines():
                        if line:
                            decoded_line = line.decode('utf-8').replace('data: ', '')
                            try:
                                chunk = json.loads(decoded_line)
                                event = chunk.get("event")

                                if "conversation_id" in chunk:
                                    st.session_state.conversation_id = chunk["conversation_id"]

                                # Chatflowのテキスト抽出
                                if event in ["message", "text_chunk"]:
                                    # event="message" は Chatbot 用、"text_chunk" は Workflow/Chatflow用
                                    content = chunk.get("answer", "") or chunk.get("data", {}).get("text", "")
                                    full_response += content
                                    response_placeholder.markdown(full_response + "▌")
                                
                                elif event == "message_end":
                                    # 最終的な回答が別に含まれる場合があるため念のため確認
                                    metadata = chunk.get("metadata", {})

                            except json.JSONDecodeError:
                                continue

                    response_placeholder.markdown(full_response)
                    if full_response:
                        st.session_state.messages.append({"role": "assistant", "content": full_response})

            except Exception as e:
                st.error(f"接続エラー: {e}")

            # --- ログ保存と音声出力 (変更なし) ---
            if full_response:
                try:
                    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
                    existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
                    new_row_df = pd.DataFrame([{
                        "date": now, "user_id": username, "user_input": user_input,
                        "ai_response": full_response, "conversation_id": st.session_state.conversation_id
                    }])
                    updated_df = pd.concat([existing_data, new_row_df], ignore_index=True)
                    conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
                except:
                    pass

                with st.spinner('音声を生成中...'):
                    try:
                        tts_res = client.audio.speech.create(model="tts-1", voice="alloy", input=full_response[:4096])
                        st.audio(io.BytesIO(tts_res.content), format="audio/mp3", autoplay=True)
                    except Exception as e:
                        st.error(f"音声エラー: {e}")
            else:
                st.warning("AIからの回答が空でした。")
