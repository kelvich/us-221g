import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import create_engine
import json
from datetime import datetime

# Initialize connection to postgres
@st.cache_resource
def init_connection():
    return create_engine('postgresql://localhost/lawsuits')

# Load data with caching
@st.cache_data
def load_data():
    conn = init_connection()
    query = """
        SELECT 
            filed,
            law360_data,
            gpt_summary,
            agency_manually_set,
            title
        FROM lawsuits
        WHERE gpt_summary is not null and law360_data is not null
    """
    df = pd.read_sql(query, conn)
    
    # Convert filed to datetime
    df['filed'] = pd.to_datetime(df['filed'])
    df['filed_date'] = df['filed'].dt.date
    
    # Parse JSON columns - handle potential string representations
    df['gpt_summary'] = df['gpt_summary'].apply(lambda x: json.loads(x) if isinstance(x, str) else x)
    df['law360_data'] = df['law360_data'].apply(lambda x: json.loads(x) if isinstance(x, str) else x)
    
    # Extract relevant fields
    df['country'] = df['gpt_summary'].apply(lambda x: x.get('likely_country_of_origin', 'Unknown'))
    df['agency_manually_set'] = df['agency_manually_set'].apply(lambda x: x if x is not None else 'Unknown')
    df['defendant_agencies'] = df['gpt_summary'].apply(lambda x: x.get('defendant_agency', []))
    
    # Ensure defendant_agencies is always a list and contains no None values
    df['defendant_agencies'] = df['defendant_agencies'].apply(lambda x: [str(a) for a in (x if isinstance(x, list) else []) if a is not None])
    
    # Explode defendant agencies to handle multiple agencies per case
    df_exploded = df.explode('defendant_agencies')
    
    return df, df_exploded

# App layout
st.title('Immigration Lawsuits Dashboard')

# Load data
df, df_exploded = load_data()

# Sidebar filters
st.sidebar.header('Filters')

# Date range filter
min_date = df['filed_date'].min()
max_date = df['filed_date'].max()
date_range = st.sidebar.date_input(
    "Select Date Range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date
)

# Country filter
countries = sorted(df['country'].unique())
selected_countries = st.sidebar.multiselect(
    'Select Countries',
    countries,
    default=[]
)

# Agency filter
agencies = sorted(df['agency_manually_set'].unique())
selected_agencies = st.sidebar.multiselect(
    'Select Defendant Agencies',
    agencies,
    default=[]
)

# Filter data
date_mask = (df['filed_date'] >= date_range[0]) & (df['filed_date'] <= date_range[1])

# Only apply country and agency filters if they are selected
if selected_countries:
    country_mask = df['country'].isin(selected_countries)
else:
    country_mask = pd.Series(True, index=df.index)

if selected_agencies:
    agency_mask = df['agency_manually_set'].isin(selected_agencies)
else:
    agency_mask = pd.Series(True, index=df.index)

filtered_df = df[date_mask & country_mask & agency_mask]

# Time series plot
st.header('Lawsuits Over Time')

# Create a complete date range for all months
date_range_complete = pd.date_range(
    start=filtered_df['filed'].dt.to_period('M').min().to_timestamp(),
    end=filtered_df['filed'].dt.to_period('M').max().to_timestamp(),
    freq='M'
)

# Group data by month
filtered_df['month'] = filtered_df['filed'].dt.to_period('M')

# Create different visualizations based on whether countries or agencies are selected
if selected_countries or selected_agencies:
    # Determine which field to use for stacking
    stack_field = 'country' if selected_countries else 'agency_manually_set'
    stack_title = 'Country' if selected_countries else 'Agency'
    
    # Group by month and stacking field
    time_series = (
        filtered_df
        .groupby(['month', stack_field])
        .size()
        .reset_index(name='count')
    )
    
    # Create a complete date range for each category
    categories = filtered_df[stack_field].unique()
    complete_index = pd.MultiIndex.from_product(
        [pd.period_range(date_range_complete[0], date_range_complete[-1], freq='M'), categories],
        names=['month', stack_field]
    )
    
    # Reindex with complete date range and fill missing values with 0
    time_series = (
        time_series
        .set_index(['month', stack_field])
        .reindex(complete_index, fill_value=0)
        .reset_index()
    )
    
    # Convert index level 'month' to string for plotting
    time_series['month_str'] = time_series['month'].astype(str)
    
    fig = px.area(
        time_series,
        x='month_str',
        y='count',
        color=stack_field,
        title=f'Number of Lawsuits by Month and {stack_title}',
        template='simple_white'
    )
else:
    # Simple area plot for total counts
    time_series = (
        filtered_df
        .groupby('month')
        .size()
        .reset_index(name='count')
    )
    
    # Create a complete date range and fill missing values with 0
    complete_dates = pd.period_range(
        date_range_complete[0],
        date_range_complete[-1],
        freq='M'
    )
    
    time_series = (
        time_series
        .set_index('month')
        .reindex(complete_dates, fill_value=0)
        .reset_index()
    )
    
    # Convert index to datetime for proper date formatting
    time_series = time_series.reset_index()
    time_series['month_str'] = time_series['index'].dt.strftime('%Y-%m')
    
    fig = px.area(
        time_series,
        x='month_str',
        y='count',
        title='Total Number of Lawsuits by Month'
    )
    
    # Update layout with adaptive tick formatting
    fig.update_layout(
        xaxis=dict(
            rangeslider=dict(visible=False),
            type='date',
            tickformatstops=[
                dict(dtickrange=[None, "M1"], value="%b %Y"),  # Monthly format when zoomed in
                dict(dtickrange=["M1", "M6"], value="%b %Y"),  # Monthly format for medium range
                dict(dtickrange=["M6", None], value="%Y"),     # Only year for wider ranges
            ]
        )
    )
    
    # Add markers on top of the area
    fig.add_scatter(
        x=time_series['month_str'],
        y=time_series['count'],
        mode='markers',
        marker=dict(color='black', size=8),
        name='Monthly Count'
    )

# Common layout updates
fig.update_layout(
    xaxis_title="Filing Month",
    yaxis_title="Number of Lawsuits",
    hovermode='x unified',
    xaxis={'tickangle': 45},
    yaxis={'rangemode': 'nonnegative'},
    showlegend=True if selected_countries or selected_agencies else False
)

st.plotly_chart(fig)

# Summary statistics
st.header('Summary Statistics')
col1, col2, col3 = st.columns(3)

with col1:
    st.metric('Total Lawsuits', len(filtered_df))
    
with col2:
    st.metric('Countries Involved', len(filtered_df['country'].unique()))

with col3:
    max_date = filtered_df['filed_date'].max()
    min_date = filtered_df['filed_date'].min()
    if pd.notna(max_date) and pd.notna(min_date):
        days_diff = (max_date - min_date).days if hasattr(max_date - min_date, 'days') else (max_date - min_date)
        avg_lawsuits = round(len(filtered_df) / max(1, days_diff), 1)
    else:
        avg_lawsuits = 0
    st.metric('Average Lawsuits per Day', avg_lawsuits)

# Sample data table with pagination
st.header('Selected Cases')
sample_columns = ['filed_date', 'country', 'defendant_agencies', 'title']

# Pagination setup
rows_per_page = 30
total_rows = len(filtered_df)
total_pages = (total_rows + rows_per_page - 1) // rows_per_page

# Initialize pagination state
if 'page_number' not in st.session_state:
    st.session_state.page_number = 1

# Create three columns for pagination controls with reduced spacing
left_col, center_col, right_col = st.columns([1, 2, 1])

# Previous page button
with left_col:
    if st.button("← Previous", disabled=(st.session_state.page_number == 1)):
        st.session_state.page_number -= 1

# Page indicator (with reduced vertical padding)
with center_col:
    st.markdown(
        f"<p style='text-align: center; margin: 0; padding: 5px;'>Page {st.session_state.page_number} of {total_pages}</p>", 
        unsafe_allow_html=True
    )

# Next page button
with right_col:
    if st.button("Next →", disabled=(st.session_state.page_number == total_pages)):
        st.session_state.page_number += 1

# Calculate start and end indices for the current page
start_idx = (st.session_state.page_number - 1) * rows_per_page
end_idx = min(start_idx + rows_per_page, total_rows)

# Display paginated data with custom height
st.dataframe(
    filtered_df[sample_columns]
    .sort_values('filed_date', ascending=False)
    .iloc[start_idx:end_idx],
    use_container_width=True,
    height=1090  # Increased height to fit 50 rows without scrolling
)

# Download filtered data
filtered_df_download = filtered_df[['filed_date', 'country', 'defendant_agencies', 'title']]
csv = filtered_df_download.to_csv(index=False)
st.download_button(
    label="Download filtered data as CSV",
    data=csv,
    file_name="immigration_lawsuits.csv",
    mime="text/csv"
)
