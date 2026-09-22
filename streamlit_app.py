import streamlit as st
import requests
import uuid

RASA_URL = "http://localhost:5005/webhooks/rest/webhook"
RASA_HEALTH_URL = "http://localhost:5005/"
RASA_TIMEOUT = 5

st.set_page_config(page_title="Sustainable Trip Planner", page_icon="✈️", layout="wide")

st.sidebar.title("Navigation")
st.sidebar.markdown("Use this panel to configure the bot")
page = st.sidebar.selectbox("Go to", ["Home", "Chat", "About"])

if page == "Chat":
    if st.sidebar.button("Clear Chat History and Start Over"):
        st.session_state["messages"] = []
        st.session_state["rasa_sender_id"] = f"streamlit_{uuid.uuid4().hex}"

st.title("Sustainable Trip Planner Bot")
st.divider()

if page == "Home":
    st.header("Welcome")
    st.write("Ask for a sustainable trip recommendation, choose a destination, or speak to a human advisor.")

elif page == "Chat":
    st.header("Chat")
    st.caption("Carbon estimates use live Climatiq emission factors when available.")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []
    if "rasa_sender_id" not in st.session_state:
        st.session_state["rasa_sender_id"] = f"streamlit_{uuid.uuid4().hex}"

    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.write(message["content"])

    user_input = st.chat_input("Type your message here:")

    st.write("Try a prompt:")
    quick_prompts = [
        ("Climatiq flight estimate", "What is the carbon footprint of a flight from London to Paris?"),
        ("Climatiq car estimate", "Estimate emissions for 500 km by car"),
        ("Climatiq train estimate", "How much carbon does a 750 km train trip produce?"),
        ("Climatiq bus estimate", "Calculate the emissions for a 300 km bus trip"),
        ("Plan Kyoto", "I want to visit Kyoto"),
        ("Plan Lisbon", "I want to visit Lisbon"),
        ("Travel Karachi", "I want to travel to Karachi"),
        ("Human advisor", "I need a human advisor"),
    ]

    cols = st.columns(4)
    for col, (label, prompt_text) in zip(cols, quick_prompts):
        with col:
            if st.button(label, key=f"quick_{label}"):
                user_input = prompt_text

    cols = st.columns(3)
    for col, (label, prompt_text) in zip(cols, quick_prompts[4:]):
        with col:
            if st.button(label, key=f"quick_{label}"):
                user_input = prompt_text

    st.write("Travel tools:")
    travel_prompts = [
        ("Travel Berlin", "I want to travel to Berlin"),
        ("Berlin to Bangalore schedule", "What is the flight schedule from Berlin to Bangalore?"),
        ("Berlin to Bangalore km", "How many kilometres is it from Berlin to Bangalore?"),
        ("Travel Paris", "I want to travel to Paris"),
        ("Berlin to Paris km", "How many kilometres is it from Berlin to Paris?"),
        ("Berlin to Paris CO2", "What are the CO2 emissions for a flight from Berlin to Paris?"),
        ("Berlin to London km", "How many kilometres is it from Berlin to London?"),
        ("Berlin to London CO2", "What are the CO2 emissions for a flight from Berlin to London?"),
    ]
    cols = st.columns(3)
    for col, (label, prompt_text) in zip(cols, travel_prompts):
        with col:
            if st.button(label, key=f"travel_{label}"):
                user_input = prompt_text
    cols = st.columns(3)
    for col, (label, prompt_text) in zip(cols, travel_prompts[3:]):
        with col:
            if st.button(label, key=f"travel_{label}"):
                user_input = prompt_text

    cols = st.columns(2)
    for col, (label, prompt_text) in zip(cols, travel_prompts[6:]):
        with col:
            if st.button(label, key=f"travel_{label}"):
                user_input = prompt_text

    if user_input:
        st.session_state["messages"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.write(user_input)

        rasa_replies = []
        try:
            requests.get(RASA_HEALTH_URL, timeout=3)
            with st.spinner("Bot is thinking..."):
                response = requests.post(
                    RASA_URL,
                    json={
                        "sender": st.session_state["rasa_sender_id"],
                        "message": user_input,
                    },
                    timeout=RASA_TIMEOUT,
                )
            response.raise_for_status()
            rasa_replies = response.json()
            reply_parts = []
            for rasa_reply in rasa_replies:
                if rasa_reply.get("text"):
                    reply_parts.append(rasa_reply["text"])
                for button in rasa_reply.get("buttons", []):
                    reply_parts.append(f"[{button['title']}]" )
            reply = "\n\n".join(reply_parts) if reply_parts else "I didn't understand that."
        except requests.exceptions.RequestException:
            reply = "Could not reach the bot. Please ensure the Rasa server is running on port 5005."
        except Exception:
            reply = "The bot is busy right now. Please try again in a moment."

        st.session_state["messages"].append({"role": "assistant", "content": reply})
        with st.chat_message("assistant"):
            if any("Estimated impact:" in rasa_reply.get("text", "") for rasa_reply in rasa_replies):
                st.caption("Climatiq estimate")
            st.write(reply)

elif page == "About":
    st.header("About")
    st.write("Built with Rasa 3.6.21 and Streamlit for a sustainable travel assistant.")
