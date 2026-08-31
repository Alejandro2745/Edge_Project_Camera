/*
  rele_control.ino
  -----------------
  Etapa 5 (Actuacion) en hardware real.
  Recibe por el puerto serial (USB) los comandos "ON" / "OFF" enviados
  desde el programa Python (actuador.py) y controla un relé o un LED
  que representa la luminaria.

  Conexion sugerida en protoboard:
    - Pin 8  -> IN del módulo relé (o resistencia 220ohm + LED si no
                tienes relé, para simular el bombillo)
    - GND    -> GND del relé / cátodo del LED
    - VCC    -> 5V del relé (si aplica)

  Compatible con Arduino Uno, Nano, o un ESP32 (cambia el pin si es
  necesario). También es el mismo código que puedes cargar en un
  Arduino Uno simulado en Wokwi (wokwi.com) si no cuentas con hardware.
*/

const int PIN_RELE = 8;
String comando = "";

void setup() {
  pinMode(PIN_RELE, OUTPUT);
  digitalWrite(PIN_RELE, LOW);
  Serial.begin(9600);
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      comando.trim();
      if (comando == "ON") {
        digitalWrite(PIN_RELE, HIGH);
        Serial.println("ACK:ON");
      } else if (comando == "OFF") {
        digitalWrite(PIN_RELE, LOW);
        Serial.println("ACK:OFF");
      }
      comando = "";
    } else {
      comando += c;
    }
  }
}
