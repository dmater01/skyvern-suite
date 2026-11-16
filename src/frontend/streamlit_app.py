import asyncio
import json
import streamlit as st
from src.core.introspect import introspect_webpage
from src.core.executor import run_autonomous_action

st.set_page_config(page_title="Skyvern URL-to-App Studio", page_icon="⚡", layout="wide")

if "app_spec" not in st.session_state:
    st.session_state.app_spec = None
if "target_url" not in st.session_state:
    st.session_state.target_url = ""

st.title("⚡ Skyvern Dynamic URL-to-App Studio")
st.caption("Inspect any website and automatically synthesize a working interactive frontend.")

st.subheader("1. Target Webpage & Objective")
col1, col2 = st.columns([2, 1])

with col1:
    url_input = st.text_input("Target URL", placeholder="https://news.ycombinator.com/jobs")
    objective_input = st.text_input("What task should this app perform?", placeholder="Search and filter for specific engineering roles")

with col2:
    st.write("##")
    if st.button("Generate Dynamic App", type="primary"):
        if not url_input or not objective_input:
            st.error("Please provide both a valid URL and task objective.")
        else:
            with st.spinner("Skyvern is inspecting the page layout and synthesizing input controls..."):
                try:
                    spec = asyncio.run(introspect_webpage(url_input, objective_input))
                    st.session_state.app_spec = spec
                    st.session_state.target_url = url_input
                    st.success("App specification generated successfully!")
                except Exception as e:
                    st.error(f"Inspection error: {e}")

st.divider()

if st.session_state.app_spec:
    spec = st.session_state.app_spec
    st.subheader(f"2. {spec.get('app_title', 'Generated Application')}")
    st.write(spec.get("description", ""))

    with st.form("dynamic_generated_form"):
        form_data = {}
        inputs = spec.get("required_inputs", [])

        for item in inputs:
            field_name = item.get("field_name")
            label = item.get("label", field_name)
            input_type = item.get("input_type", "text")
            options = item.get("options", [])
            placeholder = item.get("placeholder", "")

            if input_type == "number":
                form_data[field_name] = st.number_input(label=label, key=f"field_{field_name}")
            elif input_type == "dropdown" and options:
                form_data[field_name] = st.selectbox(label=label, options=options, key=f"field_{field_name}")
            else:
                form_data[field_name] = st.text_input(label=label, placeholder=placeholder, key=f"field_{field_name}")

        submitted = st.form_submit_button("Run Automation")

        if submitted:
            missing_fields = [
                item.get("label") for item in inputs
                if item.get("is_required") and not form_data.get(item.get("field_name"))
            ]

            if missing_fields:
                st.warning(f"Please fill out required fields: {', '.join(missing_fields)}")
            else:
                template = spec.get("prompt_template", "")
                try:
                    interpolated_prompt = template.format(**form_data)
                except KeyError:
                    interpolated_prompt = f"{spec.get('description')}\nInputs: {json.dumps(form_data)}"

                st.info(f"Executing prompt: `{interpolated_prompt}`")

                with st.spinner("Skyvern autonomous agent is executing the task..."):
                    try:
                        result = asyncio.run(run_autonomous_action(st.session_state.target_url, interpolated_prompt))
                        st.success("Task completed!")
                        extracted_info = result.get("extracted_information") or result
                        st.json(extracted_info)
                    except Exception as err:
                        st.error(f"Execution error: {err}")

    with st.expander("View Generated App Schema (JSON)"):
        st.json(st.session_state.app_spec)