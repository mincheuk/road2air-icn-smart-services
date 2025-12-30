import logging
import os
import requests
import xml.etree.ElementTree as ET
import json
from azure.eventhub import EventHubProducerClient, EventData
from openai import AzureOpenAI
from azure.functions import DocumentList
import azure.functions as func
import certifi
import pandas as pd
from azure.cosmos import CosmosClient
from azure.core.credentials import AzureKeyCredential
from datetime import datetime,timedelta

app = func.FunctionApp()  # ✅ 반드시 전역에 선언

# ✅ 환경변수 로드 - 날씨 정보용
WEATHER_EVENT_HUB_NAME = os.environ.get("weather_EVENT_HUB_NAME")
WEATHER_SERVICE_KEY = os.environ.get("weather_SERVICE_KEY")
# ✅ 도착지 도시명 리스트 (확장 가능)
TARGET_AIRPORT_CODES = {"NRT","KIX","FUK","HKG","PVG","TPE","SIN","BKK","MNL","TAO","LAX"}
# ✅ 지연 키워드 리스트 (확장 가능)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
DELAY_KEYWORDS = ["지연", "연착", "취소", "결항", "변경", "지체"]

def fetch_all_departures(service_key):
    url = "http://apis.data.go.kr/B551177/StatusOfPassengerWorldWeatherInfo/getPassengerDeparturesWorldWeather"
    all_items = []
    page = 1
    while True:
        params = {
            "serviceKey": service_key,
            "numOfRows": "100",
            "pageNo": str(page),
            "from_time": "0000",
            "to_time": "2400",
            "airport": "",
            "lang": "K",
            "type": "json"
        }

        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        items_container = data.get("response", {}).get("body", {}).get("items", {})

        if isinstance(items_container, dict):
            items = items_container.get("item", [])
            if isinstance(items, dict):
                items = [items]
        elif isinstance(items_container, list):
            items = items_container
        else:
            items = []

        if not items:
            break

        all_items.extend(items)

        total_count = data.get("response", {}).get("body", {}).get("totalCount", 0)
        if page * 100 >= total_count:
            break
        page += 1

    filtered_items = [item for item in all_items if item.get("airportCode") in TARGET_AIRPORT_CODES]
    return filtered_items

def get_latest_weekday_date():
    today = datetime.now()
    weekday = today.weekday()  # 월=0, ..., 일=6

    if weekday == 5:  # 토요일
        adjusted_date = today - timedelta(days=1)
    elif weekday == 6:  # 일요일
        adjusted_date = today - timedelta(days=2)
    else:
        adjusted_date = today

    return adjusted_date.strftime("%Y%m%d")

def check_delay_keywords(remark):
    """remark에서 지연 관련 키워드 체크"""
    if not remark:
        return False
   
    remark_lower = remark.lower()
    for keyword in DELAY_KEYWORDS:
        if keyword in remark_lower:
            return True
    return False

def format_flight_message(flight_data):
    """항공편 정보를 읽기 쉬운 메시지로 포맷팅"""
    airline = flight_data.get('airline', '정보없음')
    flight_id = flight_data.get('flightId', '정보없음')
    airport = flight_data.get('airport', '정보없음')
    airport_code = flight_data.get('airportCode', '')
    schedule_time = flight_data.get('scheduleDateTime', '')
    estimated_time = flight_data.get('estimatedDateTime', '')
    remark = flight_data.get('remark', '')
    gate = flight_data.get('gatenumber', '')
   
    # 시간 포맷팅 (HHMM -> HH:MM)
    def format_time(time_str):
        if not time_str or len(time_str) < 4:
            return time_str
        return f"{time_str[:2]}:{time_str[2:]}"
   
    # 지연 상태 추출
    delay_status = ""
    for keyword in DELAY_KEYWORDS:
        if keyword in remark:
            delay_status = keyword
            break
   
    # 목적지 정보
    destination = f"{airport}({airport_code})" if airport_code else airport
   
    # 시간 정보
    schedule_formatted = format_time(schedule_time)
    estimated_formatted = format_time(estimated_time)
   
    # 메시지 구성
    if schedule_formatted and estimated_formatted:
        time_info = f"{schedule_formatted} 출발 → {estimated_formatted} 변경"
    elif schedule_formatted:
        time_info = f"{schedule_formatted} 출발"
    else:
        time_info = "시간 정보 없음"
   
    # 게이트 정보
    gate_info = f" ({gate}게이트)" if gate else ""
   
    # 최종 메시지
    message = f"{airline} {flight_id}편 {time_info} {destination}행 비행기 {delay_status}이 감지되었습니다{gate_info}"
   
    return message
 
def send_webhook_notification(flight_data):
    """지연 감지 시 웹훅 전송"""
    try:
        # 구체적인 메시지 생성
        detailed_message = format_flight_message(flight_data)
       
        webhook_payload = {
            "alert_type": "flight_delay_detected",
            "message": detailed_message,
            "flight_info": {
                "airline": flight_data.get('airline'),
                "flightId": flight_data.get('flightId'),
                "airport": flight_data.get('airport'),
                "airportCode": flight_data.get('airportCode'),
                "scheduleDateTime": flight_data.get('scheduleDateTime'),
                "estimatedDateTime": flight_data.get('estimatedDateTime'),
                "remark": flight_data.get('remark'),
                "gatenumber": flight_data.get('gatenumber')
            },
            "detected_at": datetime.now().isoformat(),
            "source": "weather_timer_trigger"
        }
       
        headers = {
            "Content-Type": "application/json"
        }
       
        response = requests.post(
            WEBHOOK_URL,
            json=webhook_payload,
            headers=headers,
            timeout=30
        )
       
        if response.status_code == 200:
            logging.info(f"✅ 웹훅 전송 성공: {flight_data.get('flightId')}")
        else:
            logging.error(f"🔴 웹훅 전송 실패: {response.status_code} - {response.text}")
           
    except Exception as e:
        logging.error(f"🔴 웹훅 전송 중 오류: {e}")

# ✅ 첫 번째 트리거: 항공편 및 날씨 (20분마다)
@app.timer_trigger(schedule="0 */20 * * * *", arg_name="mytimer", run_on_startup=False, use_monitor=False)
@app.event_hub_output(arg_name="event", event_hub_name="%weather_EVENT_HUB_NAME%", connection="EventHubConnectionString")
def weather_timer_trigger(mytimer: func.TimerRequest, event: func.Out[str]) -> None:
    logging.info("🌤️ 항공편 날씨 API 호출 및 Event Hub 전송 시작")

    try:
        flights = fetch_all_departures(WEATHER_SERVICE_KEY)
        logging.info(f"✈️ {len(flights)}개의 항공편 데이터 수집 완료")

        events = []
        delay_count = 0

        for item in flights:
            event_body = {
                "airline": item.get('airline'),
                "flightId": item.get('flightId'),
                "scheduleDateTime": item.get('scheduleDateTime'),
                "estimatedDateTime": item.get('estimatedDateTime'),
                "airport": item.get('airport'),
                "airportCode": item.get('airportCode'),
                "yoil": item.get('yoil'),
                "remark": item.get('remark'),
                "gatenumber": item.get('gatenumber'),
                "temp": item.get('temp'),
                "senstemp": item.get('senstemp'),
                "himidity": item.get('himidity'),
                "wind": item.get('wind'),
                "wimage": item.get('wimage'),
            }
           
            # ✅ 지연 키워드 체크 및 웹훅 전송
            if check_delay_keywords(item.get('remark')):
                delay_count += 1
                detailed_message = format_flight_message(item)
                logging.info(f"🚨 지연 감지: {detailed_message}")
               
                # 웹훅 URL이 설정되어 있으면 전송
                if WEBHOOK_URL:
                    send_webhook_notification(item)
                else:
                    logging.warning("⚠️ WEBHOOK_URL이 설정되지 않았습니다.")
           
            events.append(json.dumps(event_body, ensure_ascii=False))
 
        event.set(events)
        logging.info(f"✅ Event Hub 전송 완료 - 총 {delay_count}개의 지연 항공편 감지")
 
    except Exception as e:
        logging.error(f"🔴 항공편 날씨 정보 처리 중 오류: {e}")


# ✅ 두 번째 트리거: 주차장 정보 (10분마다)
@app.timer_trigger(schedule="0 */10 * * * *", arg_name="timer", run_on_startup=False, use_monitor=False)
@app.event_hub_output(arg_name="events", event_hub_name="%parking_EVENT_HUB_NAME%", connection="EventHubConnectionString")
def parking_scheduler(timer: func.TimerRequest, events: func.Out[str]) -> None:
    logging.info("🚗 주차장 스케줄러 시작")
 
    SERVICE_KEY = os.environ["parking_SERVICE_KEY"]
    URL = "http://apis.data.go.kr/B551177/StatusOfParking/getTrackingParking"
 
    params = {
        'serviceKey': SERVICE_KEY,
        'numOfRows': 17,
        'pageNo': 1,
        'type': 'json'
    }
 
    try:
        response = requests.get(URL, params=params)
        response.raise_for_status()
        data = response.json()
 
        items = data.get("response", {}).get("body", {}).get("items", [])
 
        parking_data_list = []
        for item in items:
            parking_data = {
                "floor": item.get("floor"),
                "parking": item.get("parking"),
                "parkingarea": item.get("parkingarea"),
                "datetime": item.get("datetm"),
            }
            parking_data_list.append(parking_data)
 
        if parking_data_list:
            events.set(json.dumps(parking_data_list))  # JSON 문자열로 전송
            logging.info(f"✅ 주차장 데이터 {len(parking_data_list)}건 전송 완료")

            try :
                WORKFLOW_URL = "https://prod-23.koreacentral.logic.azure.com:443/workflows/dfb36cce6a8e46ec9d50aa7f54f4f623/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=EhkkrvmAv4mFnC3o5nnYClSLsEJgO_R57NFKw-cXWcA"
                message_payload = {
                    "text": f"✅ 주차장 데이터 수집 완료 - 총 {len(parking_data_list)}건"
                }
                headers = {"Content-Type": "application/json"}
                response = requests.post(WORKFLOW_URL, headers=headers, data=json.dumps(message_payload))
                if response.status_code == 200:
                    logging.info(" Teams Workflow 알림 전송 성공")
                else:
                    logging.warning(f" Teams 알림 응답 오류: {response.status_code} - {response.text}")
            except Exception as webhook_error:
                logging.error(f" Teams Webhook 전송 실패: {webhook_error}")

        else:
            logging.warning("⚠️ 수집된 주차장 데이터가 없습니다.")
 
    except Exception as e:
        logging.error(f"🔴 주차장 정보 처리 중 오류: {e}")


# ✅ 세 번째 트리거: 승객 흐름 정보 (3시간 마다)
@app.function_name(name="timer_trigger_passenger_flow")
@app.timer_trigger(schedule="0 0 */3 * * *", arg_name="mytimer", run_on_startup=False, use_monitor=False)
@app.event_hub_output(arg_name="out_event", event_hub_name="%passenger_EVENT_HUB_NAME%", connection="EventHubConnectionString")
def passenger_flow_trigger(mytimer: func.TimerRequest, out_event: func.Out[str]) -> None:
    logging.info("🚶 승객 흐름 타이머 트리거(XML) 실행됨")
    
    passenger_SERVICE_KEY = os.environ.get("passenger_SERVICE_KEY")
    URL = "http://apis.data.go.kr/B551177/PassengerNoticeKR/getfPassengerNoticeIKR"
    all_items = []

    area_field_map = [
        ("T1-Gate-1-2", "t1sum5"),
        ("T1-Gate-3", "t1sum6"),
        ("T1-Gate-4", "t1sum7"),
        ("T1-Gate-5-6", "t1sum8"),
        ("T1-Gate-sum", "t1sumset2"),
        ("T2-Gate-1", "t2sum3"),
        ("T2-Gate-2", "t2sum4"),
        ("T2-Gate-sum", "t2sumset2")
    ]

    try:
        params = {
            "serviceKey": passenger_SERVICE_KEY,
            "type": "xml",
            "selectdate": "0"  # 오늘
        }

        response = requests.get(URL, params=params, timeout=10)
        response.raise_for_status()

        logging.info(f"XML 응답 샘플 (raw):\n{response.text[:500]}")
        
        root = ET.fromstring(response.content)
        items = root.findall(".//item")

        if not items:
            logging.warning("XML 응답에 <item> 없음")

        for item in items:
            try:
                if item.findtext("adate") == "합계":
                    continue

                # 날짜 처리
                adate_str = item.findtext("adate") or "0"
                try:
                    date_val = int(adate_str)
                except ValueError:
                    date_val = 0

                # 시간 처리
                atime_str = item.findtext("atime") or "00_00"
                try:
                    hr_val = int(atime_str.split("_")[0])
                except (ValueError, IndexError):
                    hr_val = 0

                # 각 area별로 나누어 record 생성
                for area_name, xml_field in area_field_map:
                    try:
                        count_val = float(item.findtext(xml_field) or 0)
                    except ValueError:
                        count_val = 0.0

                    record = {
                        "date": date_val,
                        "hr": hr_val,
                        "area": area_name,
                        "customer_count": count_val
                    }
                    all_items.append(record)

            except Exception as parse_error:
                logging.warning(f"항목 파싱 실패 (건너뜀): {parse_error}")

        try:
            json_body = json.dumps(all_items, ensure_ascii=False, default=str)
            logging.info(f"직렬화된 JSON 크기: {len(json_body.encode('utf-8'))} bytes")
            
            if all_items:
                out_event.set(json_body)
                logging.info(f"✅ XML 기반 승객 데이터 {len(all_items)}건 전송 완료")
            else:
                logging.warning("⚠️ XML 기반 승객 흐름 데이터 없음")
        except Exception as send_error:
            logging.error(f"❌ Event Hub 전송 실패 또는 직렬화 오류: {send_error}")

    except Exception as e:
        logging.error(f"❌ XML API 오류: {e}")
 

# ✅ 네 번째 트리거: 환율 정보 (12시간 마다)
# 'getExchangeRateTimer' 함수 부분입니다.
 
@app.timer_trigger(schedule="0 0 */12 * * *", arg_name="myTimer", run_on_startup=False, use_monitor=False)
@app.event_hub_output(arg_name="outputEventHubMessage",
                        event_hub_name="%exchange_EVENT_HUB_NAME%",
                        connection="EventHubConnectionString")
def getExchangeRateTimer(myTimer: func.TimerRequest, outputEventHubMessage: func.Out[str]) -> None:
    logging.info('💰 환율 정보 타이머 트리거 실행')
 
    # 필터링할 통화 코드 목록
    TARGET_CURRENCIES = {"JPY(100)", "CNH", "SGD", "HKD", "THB", "USD"}
 
    try:
        # 1. API 호출
        auth_key = os.environ.get("exchange_SERVICE_KEY")
        search_date = get_latest_weekday_date()
        api_url = f"https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON?authkey={auth_key}&searchdate={search_date}&data=AP01"
 
        response = requests.get(api_url, verify=False)
        response.raise_for_status()
        exchange_data = response.json()
 
        if not exchange_data:
            logging.warning("API data is empty.")
            return
 
        # 2. 데이터 가공 및 필터링
        df = pd.DataFrame(exchange_data)
       
        # 필터링을 위해 원본 컬럼명 사용
        filtered_df = df[df['cur_unit'].isin(TARGET_CURRENCIES)].copy()
 
        # 필요한 컬럼만 선택하고 한글로 이름 변경
        filtered_df = filtered_df[['cur_unit', 'cur_nm', 'ttb', 'tts', 'deal_bas_r']]
        filtered_df.columns = ['통화코드', '통화명', '송금받을때', '송금보낼때', '매매기준율']
       
        # 숫자 형식으로 변환
        for col in ['송금받을때', '송금보낼때', '매매기준율']:
            filtered_df[col] = filtered_df[col].str.replace(',', '').astype(float)
       
        if filtered_df.empty:
            logging.warning("필터링 후 남은 데이터가 없습니다.")
            return
           
        # 3. 데이터프레임을 하나의 JSON 배열로 만들어 Event Hub로 전송
        json_str = filtered_df.to_json(orient='records', lines=False, force_ascii=False)
        json_obj = json.loads(json_str)
       
        # ensure_ascii=False는 한글 값(통화명)이 깨지지 않도록 합니다.
        outputEventHubMessage.set(json.dumps(json_obj, ensure_ascii=False))
 
        logging.info(f"✅ Successfully processed and sent {len(json_obj)} events in a single message to Event Hub.")
 
    except Exception as e:
        logging.error(f"🔴 An unexpected error occurred: {e}")
 
# ✅ 다섯 번째 트리거: 공항 시설 정보 (매주 월요일 0시)
@app.function_name(name="AirportFacilityTrigger")
@app.timer_trigger(schedule="0 0 0 * * 1", arg_name="myTimer", run_on_startup=True, use_monitor=False)
@app.event_hub_output(
    arg_name="outputEventHubMessage",
    event_hub_name=os.environ.get("commerce_EVENT_HUB_NAME"),
    connection="EventHubConnectionString"
)
def main(myTimer: func.TimerRequest, outputEventHubMessage: func.Out[str]) -> None:
    logging.info("✈️ 공항 상업시설 타이머 트리거 실행")

    url = "http://apis.data.go.kr/B551177/StatusOfFacility/getFacilityKR"
    service_key = os.environ.get("commerce_SERVICE_KEY")

    if myTimer.past_due:
        logging.warning("⏰ 타이머가 지연되어 실행되었습니다.")

    try:
        result = []
        page_no = 1
        num_of_rows = 100

        while True:
            params = {
                "serviceKey": service_key,
                "type": "xml",
                "numOfRows": num_of_rows,
                "pageNo": page_no
            }

            response = requests.get(url, params=params, timeout=15, verify=certifi.where())
            response.raise_for_status()

            root = ET.fromstring(response.content)
            items = root.findall(".//item")

            if not items:
                logging.info(f"📄 더 이상 항목 없음. 페이지 {page_no}에서 종료.")
                break

            logging.info(f"📥 페이지 {page_no}에서 {len(items)}건 수신")

            for item in items:
                try:
                    parsed_item = {
                        "entrpskoreannm": item.findtext("entrpskoreannm", ""),
                        "trtmntprdlstkoreannm": item.findtext("trtmntprdlstkoreannm", ""),
                        "lckoreannm": item.findtext("lckoreannm", ""),
                        "servicetime": item.findtext("servicetime", ""),
                        "arrordep": item.findtext("arrordep", ""),
                        "tel": item.findtext("tel", "")
                    }
                    result.append(parsed_item)
                except Exception as parse_error:
                    logging.warning(f"❌ 항목 파싱 실패 (건너뜀): {parse_error}")

            page_no += 1  # 다음 페이지로 이동

        json_data = json.dumps(result, ensure_ascii=False, default=str)
        outputEventHubMessage.set(json_data)

        logging.info(f"📤 전체 데이터 전송 완료 ({len(result)}건). 예시: {json_data[:300]}")

    except Exception as e:
        logging.error(f"🚨 API 호출 또는 파싱 실패: {e}")
        outputEventHubMessage.set("[]")