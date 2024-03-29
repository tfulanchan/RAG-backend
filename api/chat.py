from langchain.vectorstores import ElasticsearchStore
from llm_integrations import get_llm
from elasticsearch_client import (
    elasticsearch_client,
    get_elasticsearch_chat_message_history,
)
from flask import render_template, stream_with_context, current_app
import json
import os

INDEX = os.getenv("ES_INDEX", "workplace-app-docs")
INDEX_CHAT_HISTORY = os.getenv(
    "ES_INDEX_CHAT_HISTORY", "workplace-app-docs-chat-history"
)
ELSER_MODEL = os.getenv("ELSER_MODEL", ".elser_model_2")
SESSION_ID_TAG = "[SESSION_ID]"
SOURCE_TAG = "[SOURCE]"
DONE_TAG = "[DONE]"

# As in the ingest phase, the Elasticsearch index is managed through the ElasticsearchStore integration with Langchain:
store = ElasticsearchStore(
    es_connection=elasticsearch_client,
    index_name=INDEX,
    # the SparseVectorRetrievalStrategy creates and searches sparse vectors from Elastic's ELSER model
    strategy=ElasticsearchStore.SparseVectorRetrievalStrategy(model_id=ELSER_MODEL),
)


@stream_with_context
# a generator function that streams the events described above using Flask's response streaming feature,
#  which is based on the yield keyword:
def ask_question(question, session_id):
    yield f"data: {SESSION_ID_TAG} {session_id}\n\n"
    current_app.logger.debug("Chat session ID: %s", session_id)

    chat_history = get_elasticsearch_chat_message_history(
        INDEX_CHAT_HISTORY, session_id
    )

    if len(chat_history.messages) > 0:
        # create a condensed question
        condense_question_prompt = render_template(
            'condense_question_prompt.txt', question=question,
            chat_history=chat_history.messages)
        condensed_question = get_llm().invoke(condense_question_prompt).content
    else:
        condensed_question = question

    current_app.logger.debug('Condensed question: %s', condensed_question)
    current_app.logger.debug('Question: %s', question)
    # Generating an embedding for the question, and then searching for it
    #  is all nicely abstracted away by the invoke() method of Langchain's retriever interface, 
        # which performs all of these tasks and returns the list of most relevant documents found:
    docs = store.as_retriever().invoke(condensed_question)
    for doc in docs:
        doc_source = {**doc.metadata, 'page_content': doc.page_content}
        current_app.logger.debug('Retrieved document passage from: %s', doc.metadata['name'])
        yield f'data: {SOURCE_TAG} {json.dumps(doc_source)}\n\n'

    qa_prompt = render_template('rag_prompt.txt', question=question, docs=docs,
                                chat_history=chat_history.messages)
    # To connect to LLMs, the application uses Langchain's streaming support, which nicely fits with the event streaming used in this application
    answer = ''
    for chunk in get_llm().stream(qa_prompt):
        yield f'data: {chunk.content}\n\n'
        answer += chunk.content

    yield f"data: {DONE_TAG}\n\n"
    current_app.logger.debug('Answer: %s', answer)

    chat_history.add_user_message(question)
    chat_history.add_ai_message(answer)
