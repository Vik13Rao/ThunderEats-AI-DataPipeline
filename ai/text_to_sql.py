import os
import numpy as np
import pandas as pd
import streamlit as st
import snowflake.connector
from openai import OpenAI
import json
from dotenv import load_dotenv

load_dotenv()

MODEL = "gpt-4o-mini"

FORBIDDEN_WORDS = ['drop', 'delete', 'truncate', 'alter', 'update', 'insert', 'create', 'replace', 'grant', 'revoke']

EXAMPLE_QUESTIONS = [
    "How many orders are there in total?",
    "Show the 10 restaurants with the highest delivered revenue.",
    "What is the average delivery time for delivered orders?",
    "How many orders were placed for each payment method, highest first?"
]

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SCHEMA = """
Tables available (Snowflake). Use bare table names, no database or schema prefix.

FCT_ORDERS has one row per order. Columns: order_id, order_timestamp, order_date,
customer_id, restaurant_id, city, cuisine, payment_method, order_status,
is_delivered, items_count, sales_qty, subtotal, discount, delivery_fee, gst,
sales_amount, customer_rating, delivery_time_min.
DIM_RESTAURANTS has one row per restaurant. Columns: restaurant_id,
restaurant_name, city, cuisine, rating, rating_count, cost_for_two.
DIM_CUSTOMER has one row per customer. Columns: customer_id, customer_name, email,
age, age_segment, gender, marital_status, occupation, income_band, education,
family_size.
MART_DAILY_CITY_REVENUE has one row per order_date and city. Columns: order_date,
city, orders, delivered_orders, cancel_rate, gmv, aov. gmv is delivered revenue.
MART_RESTAURANT_PERFORMANCE has one row per restaurant. Columns: restaurant_id,
restaurant_name, city, cuisine, orders, revenue, avg_customer_rating,
avg_delivery_min. revenue is delivered revenue.
MART_DELIVERY_SLA has one row per city and order_hour. Columns: city, order_hour,
delivered_orders, p50, p90. p50 and p90 are delivery-time percentiles, not averages.

When combining daily city rows across dates, sum additive measures such as orders,
delivered_orders, and gmv by city. Do not average cancel_rate or aov directly;
recompute ratios from their underlying totals. Prefer a MART_ table when its grain
and measures match the question.
"""

SYSTEM_PROMPT = f"""
You are a Snowflake SQL expert. Write ONE SELECT query that answers the question.
 
Rules:
- SELECT queries only, never modify data.
- Use bare table names (FCT_ORDERS, not THUNDER.MARTS.FCT_ORDERS).
- Add a LIMIT of 100 or less, unless the question asks for a single total.
- Reply as JSON in this exact format: {{"sql": "your query here"}}
 
{SCHEMA}
"""

@st.cache_resource
def get_connection():
    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema="MARTS",
        role = "DBT_ROLE"
    )

def generate_sql(question):
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question}
        ]
    )
    answer = response.choices[0].message.content
    sql = json.loads(answer)["sql"]

    sql = sql.replace("THUNDER.MARTS.", "").replace("THUNDER.", "")
    return sql.strip().rstrip(";")

def is_safe(sql):
    lowered = sql.lower()

    if not lowered.startswith("select") and not lowered.startswith("with"):
        return False

    for word in FORBIDDEN_WORDS:
        if word in lowered:
            return False

    return True

def run_query(sql):
    conn = get_connection()
    cursor = conn.cursor()
    return cursor.execute(sql).fetch_pandas_all()



st.title("Chat with your ThunderEats Data")
st.caption(f"Ask in English, {MODEL} writes the SQL, Snowflake runs it")

with st.sidebar:
    st.header("Example Questions")
    for q in EXAMPLE_QUESTIONS:
        st.markdown(f" - {q}")

question = st.text_input("Enter your question here", 
                         placeholder="e.g. Top 10 restaurants by revenune in Banglore")

if question:
    sql = generate_sql(question)
    st.code(sql, language="sql")

    if not is_safe(sql):
        st.error("The generated SQL is not safe to run. Please modify your question.")

    else:
        try:
            df = run_query(sql)
            st.success(f"{len(df)} rows returned")
            st.dataframe(df, hide_index=True)

            if len(df.columns) == 2 and pd.api.types.is_numeric_dtype(df.iloc[:, 1]):
                st.bar_chart(df, x=df.columns[0], y=df.columns[1])

        except Exception as e:
            st.error(f"Error running query: {e}")