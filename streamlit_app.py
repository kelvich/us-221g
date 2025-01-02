import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import create_engine, text
import json
from datetime import datetime
import os

# Initialize connection to postgres
@st.cache_resource
def init_connection():
    return create_engine(os.getenv('DATABASE_URL'))

# Load metadata for filtering (much smaller query)
@st.cache_data
def load_metadata():
    conn = init_connection()
    query = """
        SELECT DISTINCT 
            filed::date as filed_date,
            COALESCE(gpt_summary->>'likely_country_of_origin', 'Unknown') as country,
            COALESCE(agency_manually_set, 'Unknown') as agency_manually_set,
            COALESCE((gpt_summary->>'221g_score')::float, 0) as score_221g
        FROM lawsuits 
        WHERE gpt_summary is not null 
        AND law360_data is not null
    """
    return pd.read_sql(query, conn)

# Load time series data with filters
def load_time_series(start_date, end_date, countries=None, agencies=None, score_range=None):
    conn = init_connection()
    
    # First, create a complete date range with all months
    query_all_months = f"""
        WITH RECURSIVE date_range AS (
            SELECT DATE_TRUNC('month', '{start_date}'::date)::date AS month
            UNION ALL
            SELECT (month + INTERVAL '1 month')::date
            FROM date_range
            WHERE month < DATE_TRUNC('month', '{end_date}'::date)
        )
        SELECT month FROM date_range
    """
    
    # Build WHERE clause based on filters
    where_clauses = [
        "gpt_summary is not null",
        "law360_data is not null",
        f"filed::date BETWEEN '{start_date}' AND '{end_date}'"
    ]
    
    if countries:
        countries_str = "','".join(countries)
        where_clauses.append(f"gpt_summary->>'likely_country_of_origin' IN ('{countries_str}')")
    
    if agencies:
        agencies_str = "','".join(agencies)
        where_clauses.append(f"agency_manually_set IN ('{agencies_str}')")
    
    if score_range:
        where_clauses.append(f"(gpt_summary->>'221g_score')::float BETWEEN {score_range[0]} AND {score_range[1]}")
    
    where_clause = " AND ".join(where_clauses)
    
    # Query that includes all months and joins with actual data
    query = f"""
        WITH RECURSIVE date_range AS (
            SELECT DATE_TRUNC('month', '{start_date}'::date)::date AS month
            UNION ALL
            SELECT (month + INTERVAL '1 month')::date
            FROM date_range
            WHERE month < DATE_TRUNC('month', '{end_date}'::date)
        ),
        lawsuit_counts AS (
            SELECT 
                DATE_TRUNC('month', filed)::date as month,
                COUNT(*) as count
            FROM lawsuits
            WHERE {where_clause}
            GROUP BY DATE_TRUNC('month', filed)::date
        )
        SELECT 
            dr.month,
            COALESCE(lc.count, 0) as count
        FROM date_range dr
        LEFT JOIN lawsuit_counts lc ON dr.month = lc.month
        ORDER BY dr.month
    """
    
    return pd.read_sql(query, conn)

# Load paginated data
def load_paginated_data(start_date, end_date, countries=None, agencies=None, 
                       score_range=None, page=1, rows_per_page=30):
    conn = init_connection()
    
    # Build WHERE clause
    where_clauses = [
        "gpt_summary is not null",
        "law360_data is not null",
        f"filed::date BETWEEN '{start_date}' AND '{end_date}'"
    ]
    
    if countries:
        countries_str = "','".join(countries)
        where_clauses.append(f"gpt_summary->>'likely_country_of_origin' IN ('{countries_str}')")
    
    if agencies:
        agencies_str = "','".join(agencies)
        where_clauses.append(f"agency_manually_set IN ('{agencies_str}')")
    
    if score_range:
        where_clauses.append(f"(gpt_summary->>'221g_score')::float BETWEEN {score_range[0]} AND {score_range[1]}")
    
    where_clause = " AND ".join(where_clauses)
    
    offset = (page - 1) * rows_per_page
    
    # Query for paginated data
    query = f"""
        SELECT *
        FROM lawsuits
        WHERE {where_clause}
        ORDER BY filed DESC
        LIMIT {rows_per_page} OFFSET {offset}
    """
    
    return pd.read_sql(query, conn)
    
# Get total count for pagination
def get_total_count(start_date, end_date, countries=None, agencies=None, score_range=None):
    conn = init_connection()
    
    where_clauses = [
        "gpt_summary is not null",
        "law360_data is not null",
        f"filed::date BETWEEN '{start_date}' AND '{end_date}'"
    ]
    
    if countries:
        countries_str = "','".join(countries)
        where_clauses.append(f"gpt_summary->>'likely_country_of_origin' IN ('{countries_str}')")
    
    if agencies:
        agencies_str = "','".join(agencies)
        where_clauses.append(f"agency_manually_set IN ('{agencies_str}')")
    
    if score_range:
        where_clauses.append(f"(gpt_summary->>'221g_score')::float BETWEEN {score_range[0]} AND {score_range[1]}")
    
    where_clause = " AND ".join(where_clauses)
    
    query = f"""
        SELECT COUNT(*) as count 
        FROM lawsuits 
        WHERE {where_clause}
    """
    
    result = pd.read_sql(query, conn)
    return result.iloc[0]['count']

# App layout
st.title('Immigration Lawsuits Dashboard')

# Load metadata for filters
metadata_df = load_metadata()

# Sidebar filters
st.sidebar.header('Filters')

# Date range filter
min_date = metadata_df['filed_date'].min()
max_date = metadata_df['filed_date'].max()
date_range = st.sidebar.date_input(
    "Select Date Range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date
)

# Country filter
countries = sorted(metadata_df['country'].unique())
selected_countries = st.sidebar.multiselect(
    'Select Countries',
    countries,
    default=[]
)

# Agency filter
agencies = sorted(metadata_df['agency_manually_set'].unique())
selected_agencies = st.sidebar.multiselect(
    'Select Defendant Agencies',
    agencies,
    default=[]
)

# 221g Score filter
min_221g = int(metadata_df['score_221g'].min())
max_221g = int(metadata_df['score_221g'].max())
selected_221g_range = st.sidebar.slider(
    '221g Score Range',
    min_value=min_221g,
    max_value=max_221g,
    value=(min_221g, max_221g),
    step=1
)

# Load time series data with filters
time_series_df = load_time_series(
    date_range[0],
    date_range[1],
    selected_countries,
    selected_agencies,
    selected_221g_range
)

# Time series plot
st.header('Lawsuits Over Time')

fig = px.area(
    time_series_df,
    x='month',
    y='count',
    title='Total Number of Lawsuits by Month'
)

fig.add_scatter(
    x=time_series_df['month'],
    y=time_series_df['count'],
    mode='markers',
    marker=dict(color='black', size=8),
    name='Monthly Count'
)

fig.update_layout(
    xaxis_title="Filing Month",
    yaxis_title="Number of Lawsuits",
    hovermode='x unified',
    xaxis={'tickangle': 45},
    yaxis={'rangemode': 'nonnegative'}
)

st.plotly_chart(fig)

# Summary statistics
st.header('Summary Statistics')
total_count = get_total_count(
    date_range[0],
    date_range[1],
    selected_countries,
    selected_agencies,
    selected_221g_range
)

col1, col2, col3 = st.columns(3)

with col1:
    st.metric('Total Lawsuits', total_count)
    
with col2:
    countries_count = metadata_df[
        (metadata_df['filed_date'] >= date_range[0]) &
        (metadata_df['filed_date'] <= date_range[1])
    ]['country'].nunique()
    st.metric('Countries Involved', countries_count)

with col3:
    days_diff = (date_range[1] - date_range[0]).days
    avg_lawsuits = round(total_count / max(1, days_diff), 1)
    st.metric('Average Lawsuits per Day', avg_lawsuits)

# Pagination setup
rows_per_page = 30
total_pages = (total_count + rows_per_page - 1) // rows_per_page

if 'page_number' not in st.session_state:
    st.session_state.page_number = 1

# Create three columns for pagination controls
left_col, center_col, right_col = st.columns([1, 2, 1])

with left_col:
    if st.button("← Previous", disabled=(st.session_state.page_number == 1)):
        st.session_state.page_number -= 1

with center_col:
    st.markdown(
        f"<p style='text-align: center; margin: 0; padding: 5px;'>Page {st.session_state.page_number} of {total_pages}</p>", 
        unsafe_allow_html=True
    )

with right_col:
    if st.button("Next →", disabled=(st.session_state.page_number == total_pages)):
        st.session_state.page_number += 1

# Load and display paginated data
paginated_df = load_paginated_data(
    date_range[0],
    date_range[1],
    selected_countries,
    selected_agencies,
    selected_221g_range,
    st.session_state.page_number,
    rows_per_page
)

st.dataframe(
    paginated_df,
    use_container_width=True,
    height=1090
)

# Download filtered data
if st.button("Prepare Download"):
    full_data = load_paginated_data(
        date_range[0],
        date_range[1],
        selected_countries,
        selected_agencies,
        selected_221g_range,
        page=1,
        rows_per_page=total_count  # Get all rows
    )
    csv = full_data.to_csv(index=False)
    st.download_button(
        label="Download filtered data as CSV",
        data=csv,
        file_name="immigration_lawsuits.csv",
        mime="text/csv"
    )