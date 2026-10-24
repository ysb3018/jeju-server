# 제주 낚시 도우미 백엔드

API 키는 이 폴더에 저장하지 않습니다. 환경변수로 주입합니다.

## 실행

```bash
export KMA_SERVICE_KEY='공공데이터포털_인코딩_인증키'
python backend/server.py
```

Windows Git Bash:

```bash
export KMA_SERVICE_KEY='공공데이터포털_인코딩_인증키'
python backend/server.py
```

## 확인

```text
GET http://127.0.0.1:8080/api/health
GET http://127.0.0.1:8080/api/forecast/weekly
```

`/api/forecast/weekly`는 다음 KMA 서비스를 서버에서 호출합니다.

- 제주도 중기육상예보: `11G00000`
- 제주 중기기온: `11G00201`
- 제주도해상 중기예보: `12B10500`

앱에는 인증키를 넣지 않습니다. 실제 휴대폰에서 사용할 때는 서버의 LAN 주소를 앱 설정에 지정해야 합니다.
