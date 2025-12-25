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
    # ★無限ループ防止用の入力バッファ
    if "processed_input" not in st.session_state:
        st.session_state.processed_input = None

    DIFY_KEY = st.secrets["DIFY_API_KEY"]
    headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}

    # --- 初回: Difyから「開始メッセージ」を取得 ---
    if st.session_state.first_run:
        try:
            params_res = requests.get("https://api.dify.ai/v1/parameters", headers=headers, params={"user": username})
            params_res.raise_for_status()
            opening_statement = params_res.json().get("opening_statement", "こんにちは！")
            st.session_state.messages.append({"role": "assistant", "content": opening_statement})
            
            tts_init = client.audio.speech.create(model="tts-1", voice="alloy", input=opening_statement)
            st.session_state.initial_audio = tts_init.content
        except Exception as e:
            st.error(f"初期取得エラー: {e}")
        st.session_state.first_run = False
        st.rerun()

    if "initial_audio" in st.session_state:
        st.audio(io.BytesIO(st.session_state.initial_audio), format="audio/mp3", autoplay=True)
        del st.session_state.initial_audio

    # --- 履歴の表示 ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- 入力 UI ---
    # keyを指定することで、rerun時にクリアしやすくします
    audio = mic_recorder(start_prompt="⏺️ 録音開始", stop_prompt="⏹️ 停止", key='my_recorder')
    chat_input = st.chat_input("メッセージを入力...")
    
    current_input = None
    if audio and audio['bytes']:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声を解析中...'):
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_bio)
            current_input = transcript.text
    elif chat_input:
        current_input = chat_input

    # --- ★メインチャット処理 (無限ループ対策版) ---
    # 「現在の入力」があり、かつ「直前に処理した入力」と異なる場合のみ実行
    if current_input and current_input != st.session_state.processed_input:
        # 直前の入力を記録してループを阻止
        st.session_state.processed_input = current_input
        
        # 1. ユーザー発言を保存・表示
        st.session_state.messages.append({"role": "user", "content": current_input})
        with st.chat_message("user"):
            st.markdown(current_input)

        # 2. ボットの応答
        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""
            data = {
                "inputs": {}, "query": current_input, "response_mode": "streaming",
                "user": username, "conversation_id": st.session_state.conversation_id
            }
            with requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=data, stream=True) as response:
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
        
        # 3. ボットの応答を保存
        st.session_state.messages.append({"role": "assistant", "content": full_response})

        # 4. 音声とログ
        if full_response:
            tts_res = client.audio.speech.create(model="tts-1", voice="alloy", input=full_response)
            st.audio(io.BytesIO(tts_res.content), format="audio/mp3", autoplay=True)
            try:
                now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
                existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
                new_row = pd.DataFrame([{"date": now, "user_id": username, "user_input": current_input, "ai_response": full_response, "conversation_id": st.session_state.conversation_id}])
                conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=pd.concat([existing_data, new_row], ignore_index=True))
            except: pass

        # 5. 最後に再実行（これで履歴が固定され、入力バッファが効いてループが止まる）
        st.rerun()

    # 入力が空になったらバッファもクリアする
    if not current_input:
        st.session_state.processed_input = None

elif st.session_state["authentication_status"] is False:
    st.error('ログインに失敗しました')
