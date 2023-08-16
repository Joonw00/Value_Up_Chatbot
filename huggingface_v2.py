import requests
import pandas as pd
import numpy as np
import copy
import json
import torch
import pickle
import matplotlib.pyplot as plt
import sklearn.datasets as datasets
import sklearn.manifold as manifold
import openai
import os
import sys
import csv
import json

from ast import literal_eval
from sentence_transformers import SentenceTransformer, util
from transformers import AutoTokenizer, AutoModel
from transformers import OwlViTProcessor, OwlViTForObjectDetection
from transformers import pipeline
from transformers import GPT2TokenizerFast
from PIL import Image
from typing import List, Tuple, Dict
from dotenv import load_dotenv

''' OpenAI API 불러오기 '''
load_dotenv()
openai.api_key = "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"


''' 2-1 데이터 준비 '''
data = pd.read_csv('./policy_data-3.csv', sep=",", dtype=str)
data['recom_total'] = data['who'] + " / " + data['age'] + " / " + data['when'] + data['category']
# print(data.head())

# HuggingFace Embedding을 활용하여 Embdding vector 추출
# model = SentenceTransformer("monologg/koelectra-base-v2-discriminator") # 사용할 모델 교체함
model = SentenceTransformer("sentence-transformers/distiluse-base-multilingual-cased-v2")
data['recom_embeddings'] = data['recom_total'].apply(lambda x : model.encode(x))
data['desc_embeddings'] = data['title'].apply(lambda x : model.encode(x))
# print(data.shape)
# print(data.head())
# data.to_csv('./data.csv', encoding='utf-8-sig')


''' 2-3 cosine 유사도 구현 '''
top_k = 2 # 답변 개수 조절
def get_query_sim_top_k(query, model, df, top_k):
    query_encode = model.encode(query)
    cos_scores = util.pytorch_cos_sim(query_encode, df['recom_embeddings'])[0]
    top_results = torch.topk(cos_scores, k=top_k)
    return top_results

def get_overview_sim_top_k(desc, model, df, top_k):
    overview_encode = model.encode(desc)
    cos_scores = util.pytorch_cos_sim(overview_encode, df['desc_embeddings'])[0]
    top_results = torch.topk(cos_scores, k=top_k)
    return top_results

# query = "급식에 대해 경제적 지원을 해주는 해주는 고등학생을 위한 8월 정책 추천해줘"
# top_result = get_query_sim_top_k(query, model, data, top_k)
# # print(top_result)
# print(data.iloc[top_result[1].numpy(), :][['title', 'who', 'age', 'when']])



''' 2-5 ChatGPT를 활용한 추천 시스템 로직 설계, 코드 구현 '''
msg_prompt = {
    'recom' : {
                'system' : "너는 user의 질문 의도를 이해하는 데 도움이 되는 assistant입니다.",
                'user' : "당연하지!'로 시작하는 간단한 인사말 1문장을 작성해. 추천해주겠다는 말을 해줘.",
              },
    'desc' : {
                'system' : "너는 user의 질문 의도를 이해하는 데 도움이 되는 assistant입니다.",
                'user' : "'당연하지!'로 시작하는 간단한 인사말 1문장을 작성하여 user에게 정책을 설명해줘.",
              },
    'intent' : {
                'system' : "너는 user의 질문 의도를 이해하는 도움을 주는 assistant입니다.",
                'user' : "아래 문장은 'description','recommend', 중 속하는 categories만 보여라."
                }
}

user_msg_history = []

def set_prompt(intent, query, msg_prompt_init, model):
    '''prompt 형태를 만들어주는 함수'''
    m = dict()
    # 검색 또는 추천이면
    if ('recom' in intent) or ('search' in intent):
        msg = msg_prompt_init['recom'] # 시스템 메세지를 가지고오고
        # print("recommend : ", msg)
    # 설명문이면
    elif 'desc' in intent:
        msg = msg_prompt_init['desc'] # 시스템 메세지를 가지고오고
        # print("description : ", msg)
    # intent 파악
    else:
        msg = msg_prompt_init['intent']
        msg['user'] += f' {query} \n A:'
        print("intent : ", msg)

    for k, v in msg.items():
        m['role'], m['content'] = k, v
    return [m]

def get_chatgpt_msg(msg):
    completion = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=msg
                    )
    return completion['choices'][0]['message']['content']

def user_interact(query, model, msg_prompt_init):
    # 1. 사용자의 의도를 파악
    user_intent = set_prompt('intent', query, msg_prompt_init, None)
    user_intent = get_chatgpt_msg(user_intent).lower()
    print("user_intent : ", user_intent)

    # 2. 사용자의 쿼리에 따라 prompt 생성
    intent_data = set_prompt(user_intent, query, msg_prompt_init, model)
    intent_data_msg = get_chatgpt_msg(intent_data).replace("\n", "").strip()\

    # 3-1. 추천 또는 검색이면
    if ('recom' in user_intent) or ('search' in user_intent):
        recom_msg = str()

        # 기존에 메세지가 있으면 쿼리로 대체
        # if (len(user_msg_history) > 0 ) and (user_msg_history[-1]['role'] == 'assistant'):
        #     query = user_msg_history[-1]['content']['recom_total']

        # 유사 아이템 가져오기
        top_result = get_query_sim_top_k(query, model, data, top_k=1 if 'recom' in user_intent else 1)
        #print("top_result : ", top_result)

        # 검색이면, 자기 자신의 컨텐츠는 제외
        top_index = top_result[1].numpy() if 'recom' in user_intent else top_result[1].numpy()[1:]
        #print("top_index : ", top_index)

        # 정책명, 대상, 기간, 링크
        r_set_d = data.iloc[top_index, :][['title', 'who', 'when']]
        r_set_d = json.loads(r_set_d.to_json(orient="records"))

        count = 0
        recom_msg += "\n"
        for r in r_set_d:
            for _, v in r.items():
                if(count == 0):
                    recom_msg += f"{v} 정책으로 "
                elif(count == 1):
                    recom_msg += f"{v} 대상에게 "
                elif(count == 2):
                    recom_msg += f"{v} 기간 동안 시행하는 정책입니다.\n\n"
                elif(count == 3):
                    recom_msg += "자세한 설명은 아래의 링크를 클릭하여 접속해보시기 바랍니다.\n"
                    recom_msg += f"{v}\n"
                count += 1
        user_msg_history.append({'role' : 'assistant', 'content' : f"{intent_data_msg} {str(recom_msg)}"})
        print(f"{recom_msg}") 
        print("더 궁금하신 것이 있다면 다시 질문해주시면 감사하겠습니다.")

    # 3-2. 설명이면
    elif 'desc' in user_intent:
        desc_msg = str()

        top_result = get_overview_sim_top_k(query, model, data, top_k=1)
        r_set_d = data.iloc[top_result[1].numpy(), :][['title','overview','link']]
        r_set_d = json.loads(r_set_d.to_json(orient="records"))

        count = 0
        desc_msg += "\n"
        for r in r_set_d:
            for _, v in r.items():
                if(count == 0):
                    desc_msg += f"{v} 정책이란 "
                elif(count == 1):
                    desc_msg += f"{v} 하는 정책입니다.\n"
                elif(count == 2):
                    desc_msg += "자세한 설명은 아래의 링크를 클릭하여 접속해보시기 바랍니다.\n"
                    desc_msg += f"{v}\n"
                count += 1
        user_msg_history.append({'role' : 'assistant', 'content' : f"{intent_data_msg} {str(desc_msg)}"})
        print(f"{desc_msg}")
        print("더 궁금하신 것이 있다면 다시 질문해주시면 감사하겠습니다.\n")

query = input()
user_interact(query, model, copy.deepcopy(msg_prompt))
desired_answer = input("이 답변이 원하는 답변입니까? (yes/no): ")
if desired_answer.lower() == "yes":
    print("프로그램을 종료합니다.")
    exit(0)
else:
    query = input("원하는 답변이 아니라면 구체적인 키워드를 포함해서 질문해주세요: ")
    user_interact(query, model, copy.deepcopy(msg_prompt))