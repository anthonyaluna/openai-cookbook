from google.cloud import bigquery
import functions_framework
import os
from openai import OpenAI
import json

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
embeddings_model = os.getenv('EMBEDDINGS_MODEL')
project_id = os.getenv('PROJECT_ID')
dataset_id = os.getenv('DATASET_ID')
table_id = os.getenv('TABLE_ID')

def generate_embeddings(text, model):
    # Generate embeddings for the provided text using the specified model
    embeddings_response = openai_client.embeddings.create(model=model, input=text)
    # Extract the embedding data from the response
    embedding = embeddings_response.data[0].embedding
    return embedding

@functions_framework.http
def openai_docs_search(request):
    print('received a request')
    client = bigquery.Client()

    request_json = request.get_json(silent=True)

    if not isinstance(request_json, dict) or not request_json:
        return json.dumps({"error": "Invalid JSON in request"}), 400, {'Content-Type': 'application/json'}

    query = request_json.get('query')
    top_k = request_json.get('top_k', 3)
    category = request_json.get('category', '')

    if not isinstance(query, str) or not query.strip() or len(query) > 4000 or type(top_k) is not int or not 1 <= top_k <= 20 or not isinstance(category, str):
        return json.dumps({"error": "Query parameter is required"}), 400, {'Content-Type': 'application/json'}

    embedding_query = generate_embeddings(query, embeddings_model)


    sql_query = f"""
    WITH search_results AS (
        SELECT query.id AS query_id, base.id AS base_id, distance
        FROM VECTOR_SEARCH(
            TABLE `{project_id}.{dataset_id}.{table_id}`, 'content_vector',
            (SELECT @embedding AS content_vector, 'query_vector' AS id),
            top_k => @top_k, distance_type => 'COSINE', options => '{{"use_brute_force": true}}')
    )
    SELECT sr.query_id, sr.base_id, sr.distance, ed.text, ed.title, ed.category
    FROM search_results sr
    JOIN `{project_id}.{dataset_id}.{table_id}` ed ON sr.base_id = ed.id
    """

    if category:
        sql_query += " WHERE ed.category = @category"

    sql_query += " ORDER BY sr.distance;"

    parameters = [
        bigquery.ArrayQueryParameter("embedding", "FLOAT64", embedding_query),
        bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
    ]
    if category:
        parameters.append(bigquery.ScalarQueryParameter("category", "STRING", category))
    config = bigquery.QueryJobConfig(query_parameters=parameters)
    query_job = client.query(sql_query, job_config=config)

    rows = []
    for row in query_job:
        rows.append({
            "text": row.text,
            "title": row.title,
            "distance": row.distance,
            "category": row.category
        })

    response = {
        "items": rows
    }
    print('sending response')
    print(len(rows))
    return json.dumps(response), 200
