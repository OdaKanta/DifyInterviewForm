import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder
import io
import datetime
import pandas as pd
from streamlit_gsheets import GSheetsConnection

# --- 1. ユーザー認証設定 (サンプル) ---
names = ["田中 太郎", "小田 敢太"]
usernames = ["tanaka", "oda"]
# ハッシュ化されたパスワードを使用するのが推奨されますが、テスト用に平文で設定
passwords = ["pass123", "pass456"]

authenticator = stauth.Authenticate(
    {'usernames': {
        usernames[0]: {'name': names[0], 'password': passwords[0]},
        usernames[1]: {'name': names[1], 'password': passwords[1]}
    }},
    "dify_chat_cookie", "auth_key", cookie_expiry_days=30
)

# --- 2. ログイン処理 ---
authenticator.login('main')

if st.session_state["authentication_status"]:
    # 認証成功後のメインUI
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    conn = st.connection("gsheets", type=GSheetsConnection)
    
    with st.sidebar:
        st.write(f"ログイン中: {st.session_state['name']} さん")
        authenticator.logout('ログアウト', 'sidebar')

    st.title("Dify × Streamlit AI Assistant")

    # --- 3. セッション状態の初期化 ---
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""
    if "initialized" not in st.session_state:
        st.session_state.initialized = False

    DIFY_KEY = st.secrets["DIFY_API_KEY"]
    headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}

    # --- 4. ログ保存関数 ---
    def save_log(user_input, ai_response):
        try:
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
            new_row = {
                "date": now,
                "user_id": st.session_state["username"],
                "user_input": user_input,
                "ai_response": ai_response,
                "conversation_id": st.session_state.conversation_id
            }
            # 既存データ読み込みと更新
            existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
            updated_df = pd.concat([existing_data, pd.DataFrame([new_row])], ignore_index=True)
            conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
        except Exception as e:
            st.error(f"ログ保存失敗: {e}")

    # --- 5. 初回起動（Difyの挨拶取得） ---
    if not st.session_state.initialized:
        with st.spinner('システム接続中...'):
            init_data = {
                "inputs": {},  # ワークフローの開始ノードで変数が必要な場合はここに入れる
                "query": "開始", # もし開始トリガーがあるならその文言
                "response_mode": "blocking",
                "user": st.session_state["username"],
                "files": []
            }
            try:
                res = requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=init_data)
                
                # エラー内容を詳細に表示するためのデバッグ処理
                if res.status_code != 200:
                    st.error(f"Dify APIエラー: {res.status_code} - {res.text}")
                    st.stop()
    
                res_json = res.json()
                # Chatflowの場合、answerが空の場合があるため
                msg = res_json.get("answer") or "接続されました。何かお手伝いしましょうか？"
                
                st.session_state.conversation_id = res_json.get("conversation_id", "")
                st.session_state.messages.append({"role": "assistant", "content": msg})
                st.session_state.initialized = True
                
                # 音声出力
                tts = client.audio.speech.create(model="tts-1", voice="alloy", input=msg)
                st.audio(io.BytesIO(tts.content), format="audio/mp3", autoplay=True)
                st.rerun()
            except Exception as e:
                st.error(f"接続失敗: {e}")
                st.stop()

    # --- 6. チャットUIの表示 ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- 7. 入力処理（音声 & テキスト） ---
    user_input = None
    
    # 音声入力
    audio = mic_recorder(start_prompt="🎤 話す", stop_prompt="🛑 停止", key='recorder')
    if audio:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声をテキストに変換中...'):
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_bio)
            user_input = transcript.text

    # テキスト入力
    if chat_input := st.chat_input("メッセージを入力..."):
        user_input = chat_input

    # --- 8. AI応答処理 ---
    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""
            
            payload = {
                "inputs": {},
                "query": user_input,
                "response_mode": "streaming",
                "user": st.session_state["username"],
                "conversation_id": st.session_state.conversation_id,
                "files": []
            }
            
            response = requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=payload, stream=True)
            
            for line in response.iter_lines():
                if line:
                    decoded = line.decode('utf-8').replace('data: ', '')
                    try:
                        chunk = json.loads(decoded)
                        if "answer" in chunk:
                            full_response += chunk["answer"]
                            response_placeholder.markdown(full_response + "▌")
                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]
                    except:
                        continue
            
            response_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})
            
            # ログ保存
            save_log(user_input, full_response)
            
            # 音声出力
            tts_res = client.audio.speech.create(model="tts-1", voice="alloy", input=full_response)
            st.audio(io.BytesIO(tts_res.content), format="audio/mp3", autoplay=True)

elif st.session_state["authentication_status"] is False:
    st.error('ユーザー名またはパスワードが正しくありません')
elif st.session_state["authentication_status"] is None:
    st.info('ログインしてください')
