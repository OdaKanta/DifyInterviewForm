import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder
import io
from streamlit_gsheets import GSheetsConnection
import datetime
import pandas as pd

# --- 1. ユーザー情報の設定 ---
names = ["田中 太郎", "佐藤 花子"]
usernames = ["tanaka", "sato"]
passwords = ["pass123", "pass456"]

authenticator = stauth.Authenticate(
    {'usernames': {
        usernames[0]: {'name': names[0], 'password': passwords[0]},
        usernames[1]: {'name': names[1], 'password': passwords[1]}
    }},
    "dify_app_cookie", "signature_key", cookie_expiry_days=30
)

# --- 2. ログイン画面 ---
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

    # セッション状態の初期化
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""
    if "first_run" not in st.session_state:
        st.session_state.first_run = True

    DIFY_KEY = st.secrets["DIFY_API_KEY"]
    headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}

    # --- 初回ログイン時の「開始メッセージ」取得ロジック ---
    if st.session_state.first_run:
        try:
            params_res = requests.get("https://api.dify.ai/v1/parameters", headers=headers, params={"user": username})
            params_res.raise_for_status()
            params_data = params_res.json()
            opening_statement = params_data.get("opening_statement", "")
            
            if opening_statement:
                st.session_state.messages.append({"role": "assistant", "content": opening_statement})
                if len(opening_statement.strip()) > 0:
                    tts_init = client.audio.speech.create(model="tts-1", voice="alloy", input=opening_statement)
                    st.session_state.initial_audio = tts_init.content # 音声をセッションに保存
            else:
                fallback_msg = "こんにちは！何かお手伝いしましょうか？"
                st.session_state.messages.append({"role": "assistant", "content": fallback_msg})
                tts_init = client.audio.speech.create(model="tts-1", voice="alloy", input=fallback_msg)
                st.session_state.initial_audio = tts_init.content
                
        except Exception as e:
            st.error(f"初期設定取得エラー: {e}")
        
        st.session_state.first_run = False
        st.rerun()

    # 初回音声を再生（rerun後に一度だけ再生されるようにする）
    if "initial_audio" in st.session_state:
        st.audio(io.BytesIO(st.session_state.initial_audio), format="audio/mp3", autoplay=True)
        del st.session_state.initial_audio

    # --- 履歴の描画 (常に行う) ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- 入力 UI ---
    st.write("話しかけてください：")
    audio = mic_recorder(start_prompt="⏺️ 録音開始", stop_prompt="⏹️ 停止", key='recorder')
    user_input = None

    if audio:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声を解析中...'):
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_bio)
            user_input = transcript.text
    
    chat_input = st.chat_input("またはメッセージを入力...")
    if chat_input:
        user_input = chat_input

    # --- メインチャット処理 ---
    if user_input:
        # ユーザー入力を履歴に追加
        st.session_state.messages.append({"role": "user", "content": user_input})
        # 即座に画面に表示
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""

            data = {
                "inputs": {}, "query": user_input, "response_mode": "streaming",
                "user": username, "conversation_id": st.session_state.conversation_id
            }
            response = requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=data, stream=True)

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        chunk = json.loads(decoded_line[6:])
                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]
                        if "answer" in chunk:
                            full_response += chunk["answer"]
                            response_placeholder.markdown(full_response + "▌")

            response_placeholder.markdown(full_response)
            # AIの返答を履歴に追加
            st.session_state.messages.append({"role": "assistant", "content": full_response})

            # ログ保存
            try:
                now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
                existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
                new_row = pd.DataFrame([{
                    "date": now, "user_id": username, "user_input": user_input,
                    "ai_response": full_response, "conversation_id": st.session_state.conversation_id
                }])
                updated_df = pd.concat([existing_data, new_row], ignore_index=True)
                conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
            except Exception as e:
                st.warning(f"ログ保存失敗: {e}")

            # 音声生成
            if len(full_response.strip()) > 0:
                with st.spinner('音声を生成中...'):
                    tts_res = client.audio.speech.create(model="tts-1", voice="alloy", input=full_response)
                    st.audio(io.BytesIO(tts_res.content), format="audio/mp3", autoplay=True)
            
            # ★重要: メッセージを保存した後に再描画して、履歴ループに確実に乗せる
            st.rerun()

elif st.session_state["authentication_status"] is False:
    st.error('ユーザー名またはパスワードが間違っています')
