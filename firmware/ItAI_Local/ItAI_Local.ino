/* ItAI: Wi-Fi local -> PC. Arduino-ESP32 3.x; DHT sensor library.
 * ESP32 clasico: A0 -> GPIO34; ESP32-S3: A0 -> GPIO6.
 * A0 SIEMPRE mediante divisor (10k/10k). D0 opcional, adaptado a 3.3V.
 * RAM conserva la sesion completa durante cortes Wi-Fi, NO cortes de energia.
 * No convierte ADC a ppm. El calentamiento depende del encendido, no del hogar.
 */
#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DHT.h>
#include <esp_system.h>
#include <cmath>

constexpr uint8_t PIN_DHT = 16;
#if CONFIG_IDF_TARGET_ESP32S3
constexpr uint8_t PIN_MQ = 6;
#elif CONFIG_IDF_TARGET_ESP32
constexpr uint8_t PIN_MQ = 34;
#else
#error "Seleccione ESP32 Dev Module o ESP32S3 Dev Module y verifique el cableado"
#endif
constexpr uint8_t PIN_D0 = 16;
constexpr bool USE_D0 = false; // Activar solamente si D0 tiene adaptacion de nivel.
constexpr bool CONDITIONED = false; // Cambiar tras acondicionamiento documentado.
constexpr uint32_t STARTUP_MS = 30UL * 60UL * 1000UL;
constexpr uint32_t PERIOD_MS = 2500;
constexpr uint16_t CAPACITY = 1100;
constexpr uint16_t ADC_READS = 16;
constexpr char SSID[] = "ItAI-Local";
constexpr char PASSWORD[] = "CAMBIAR_WIFI_2026"; // Cambiar antes de desplegar.
constexpr char API_KEY[] = "CONFIGURAR_CLAVE_API";

struct Reading {
  uint32_t sequence, elapsed_ms, uptime_ms;
  float temperature, humidity;
  uint16_t adc, mv, adc_min, adc_max, flags;
};
DHT dht(PIN_DHT, DHT11);
WebServer server(80);
SemaphoreHandle_t mutex;
Reading readings[CAPACITY];
uint16_t count = 0;
uint32_t sessionStart = 0, durationMs = 1800000, nextDue = 0;
uint32_t missedSlots = 0, bootId;
String sessionId;
bool active = false, startupDone = false;

bool authorized() {
  if (server.header("X-ItAI-Key") == API_KEY) return true;
  server.send(403, "application/json", "{\"error\":\"clave incorrecta\"}");
  return false;
}
String numberOrNull(float x) { return std::isfinite(x) ? String(x, 2) : "null"; }
String statusJson() {
  xSemaphoreTake(mutex, portMAX_DELAY);
  String s = "{\"protocol\":2,\"boot_id\":" + String(bootId) +
    ",\"session_id\":\"" + sessionId + "\",\"active\":" + (active ? "true" : "false") +
    ",\"count\":" + String(count) + ",\"period_ms\":" + String(PERIOD_MS) +
    ",\"duration_ms\":" + String(durationMs) + ",\"target\":" + String(durationMs / PERIOD_MS) +
    ",\"missed_slots\":" + String(missedSlots) + ",\"uptime_ms\":" + String(millis()) +
    ",\"startup_remaining_ms\":" + String(startupDone ? 0 : STARTUP_MS - min(millis(), STARTUP_MS)) +
    ",\"conditioned\":" + (CONDITIONED ? "true" : "false") + ",\"mq_pin\":" + String(PIN_MQ) + "}";
  xSemaphoreGive(mutex);
  return s;
}
bool validId(const String& id) {
  if (id.length() < 8 || id.length() > 48) return false;
  for (size_t i=0; i<id.length(); i++) if (!isalnum(id[i]) && id[i]!='-' && id[i]!='_') return false;
  return true;
}
void acquisition(void*) {
  for (;;) {
    uint32_t now = millis();
    xSemaphoreTake(mutex, portMAX_DELAY);
    if (now >= STARTUP_MS) startupDone = true;
    bool take = active && int32_t(now - nextDue) >= 0;
    uint32_t started = sessionStart;
    // A scheduling delay must not create repeated readings to fill missed slots.
    if (take && now - sessionStart > durationMs + 200) {
      active = false; take = false;
    }
    xSemaphoreGive(mutex);
    if (take) {
      Reading r = {};
      r.uptime_ms = now; r.elapsed_ms = now - started;
      r.humidity = dht.readHumidity(); r.temperature = dht.readTemperature();
      if (!std::isfinite(r.temperature) || r.temperature < -40 || r.temperature > 125) r.temperature = NAN;
      else { r.flags |= 1; if (r.temperature < 0 || r.temperature > 50) r.flags |= 256; }
      if (!std::isfinite(r.humidity) || r.humidity < 0 || r.humidity > 100) r.humidity = NAN;
      else { r.flags |= 2; if (r.humidity < 20 || r.humidity > 80) r.flags |= 512; }
      uint32_t adcSum=0, mvSum=0; uint16_t d0=0; r.adc_min=4095;
      for (uint16_t i=0;i<ADC_READS;i++) {
        uint16_t v=analogRead(PIN_MQ); adcSum+=v; mvSum+=analogReadMilliVolts(PIN_MQ);
        r.adc_min=min(r.adc_min,v); r.adc_max=max(r.adc_max,v);
        if(v<=5 || v>=4090) r.flags|=32;
        if(USE_D0 && digitalRead(PIN_D0)==LOW) d0++;
      }
      r.adc=(adcSum+ADC_READS/2)/ADC_READS; r.mv=(mvSum+ADC_READS/2)/ADC_READS; r.flags|=4;
      if(USE_D0) {r.flags|=128; if(d0>ADC_READS/2) r.flags|=8;}
      if(!startupDone) r.flags|=16;
      if(!CONDITIONED) r.flags|=64;
      xSemaphoreTake(mutex, portMAX_DELAY);
      if(active && sessionStart==started && count<CAPACITY) {
        r.sequence=count+1; readings[count++]=r;
        nextDue += PERIOD_MS;
        while(int32_t(millis()-nextDue)>=0 && nextDue-sessionStart<=durationMs) {
          missedSlots++; nextDue+=PERIOD_MS;
        }
        if(nextDue-sessionStart>durationMs || count>=CAPACITY) active=false;
      }
      xSemaphoreGive(mutex);
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}
void setup() {
  Serial.begin(115200); dht.begin();
  analogReadResolution(12); analogSetPinAttenuation(PIN_MQ, ADC_11db);
  if(USE_D0) pinMode(PIN_D0,INPUT);
  mutex=xSemaphoreCreateMutex();
  if(!mutex) {Serial.println("ERROR mutex"); return;}
  WiFi.mode(WIFI_AP);
  if(!WiFi.softAP(SSID,PASSWORD,1,false,2)) {Serial.println("ERROR WiFi AP"); return;}
  bootId=esp_random();
  const char* headers[]={"X-ItAI-Key"}; server.collectHeaders(headers,1);
  server.on("/status",HTTP_GET,[]{if(authorized()) server.send(200,"application/json",statusJson());});
  server.on("/start",HTTP_POST,[]{
    if(!authorized()) return;
    String id=server.arg("id"); long seconds=server.arg("seconds").toInt();
    if(!validId(id) || seconds<10 || seconds>2750 || (seconds*1000)%PERIOD_MS!=0) {
      server.send(400,"application/json","{\"error\":\"id o duracion invalida\"}"); return;
    }
    xSemaphoreTake(mutex,portMAX_DELAY);
    if(sessionId==id) {xSemaphoreGive(mutex); server.send(200,"application/json",statusJson()); return;}
    if(active) {xSemaphoreGive(mutex); server.send(409,"application/json","{\"error\":\"sesion activa\"}"); return;}
    count=0; missedSlots=0; sessionId=id; durationMs=seconds*1000;
    sessionStart=millis(); nextDue=sessionStart+PERIOD_MS; active=true;
    xSemaphoreGive(mutex); server.send(200,"application/json",statusJson());
  });
  server.on("/stop",HTTP_POST,[]{
    if(!authorized()) return;
    xSemaphoreTake(mutex,portMAX_DELAY);
    if(server.arg("id")!=sessionId) {xSemaphoreGive(mutex);server.send(409,"application/json","{\"error\":\"sesion distinta\"}");return;}
    active=false; xSemaphoreGive(mutex); server.send(200,"application/json",statusJson());
  });
  server.on("/samples",HTTP_GET,[]{
    if(!authorized()) return;
    long after=server.arg("after").toInt();
    xSemaphoreTake(mutex,portMAX_DELAY);
    if(server.arg("id")!=sessionId || after<0 || after>count) {
      xSemaphoreGive(mutex);server.send(409,"application/json","{\"error\":\"sesion o cursor invalido\"}");return;
    }
    String out="{\"session_id\":\""+sessionId+"\",\"boot_id\":"+String(bootId)+",\"samples\":[";
    uint16_t end=min(uint16_t(after+32),count);
    for(uint16_t i=after;i<end;i++) {
      const Reading &r=readings[i]; if(i>after) out+=",";
      out+="{\"sequence\":"+String(r.sequence)+",\"elapsed_ms\":"+String(r.elapsed_ms)+
        ",\"uptime_ms\":"+String(r.uptime_ms)+",\"temperature\":"+numberOrNull(r.temperature)+
        ",\"humidity\":"+numberOrNull(r.humidity)+",\"mq_adc\":"+String(r.adc)+
        ",\"mq_mv\":"+String(r.mv)+",\"mq_min\":"+String(r.adc_min)+
        ",\"mq_max\":"+String(r.adc_max)+",\"flags\":"+String(r.flags)+"}";
    }
    out+="]}"; xSemaphoreGive(mutex); server.send(200,"application/json",out);
  });
  server.begin();
  if(xTaskCreate(acquisition,"acquisition",4096,nullptr,2,nullptr)!=pdPASS) {
    server.stop(); Serial.println("ERROR tarea adquisicion"); return;
  }
  Serial.printf("ItAI WiFi: %s | IP: %s | MQ A0 GPIO %u\n",SSID,WiFi.softAPIP().toString().c_str(),PIN_MQ);
  Serial.println("Conectar PC a esta red. Iniciar hogar desde itai.py. Mantener alimentacion entre hogares.");
}
void loop() { server.handleClient(); delay(2); }
