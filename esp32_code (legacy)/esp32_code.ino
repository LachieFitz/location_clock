#include <Arduino.h>
#include <ESP32Servo.h>

static const uint32_t BAUD = 115200;

// ---------- YOUR WIRING / NAMES ----------
static const int N_SERVOS = 4;
static const int SERVO_PINS[N_SERVOS] = {12, 25, 26, 13};
static const char* PERSON[N_SERVOS]   = {"dad","mum","lachlan","stella"};

// ---------- YOUR CALIBRATION ----------
int NEUTRAL_US[N_SERVOS]   = {1500, 1500, 1500, 1500};
int CW_US[N_SERVOS]        = {1650, 1650, 1650, 1650};   // pulse that spins CW
int CCW_US[N_SERVOS]       = {1350, 1350, 1350, 1350};   // pulse that spins CCW
unsigned long MS90_MS[N_SERVOS] = {230, 240, 260, 205};  // ms to rotate ~90° at those speeds

// FORWARD definition per-servo (true = CW is forward; false = CCW is forward)
bool FWD_IS_CW[N_SERVOS] = {false, false, false, false};

// Current quadrant per servo (0=home, 1=elsewhere, 2=travelling, 3=work/school)
int currentQuadrant[N_SERVOS] = {0,0,0,0};

Servo sv[N_SERVOS];
String line;

// ---------- helpers ----------
void hardStop(int i){ sv[i].writeMicroseconds(NEUTRAL_US[i]); }

int forwardPulse(int i){ return FWD_IS_CW[i] ? CW_US[i] : CCW_US[i]; }

int nameToIndex(const String& who){
  for(int i=0;i<N_SERVOS;++i) if(who.equalsIgnoreCase(PERSON[i])) return i;
  bool num=who.length(); for(size_t k=0;k<who.length();++k) if(!isDigit(who[k])){num=false;break;}
  if(num && who.length()){int i=who.toInt(); if(i>=0&&i<N_SERVOS) return i;}
  return -1;
}
int tokenToQuadrant(const String& tk){
  if(tk.length()==1 && tk[0]>='0' && tk[0]<='3') return tk[0]-'0';
  String t=tk; t.toLowerCase();
  if(t=="home") return 0;
  if(t=="elsewhere") return 1;
  if(t=="travelling"||t=="traveling") return 2;
  if(t=="work"||t=="school"||t=="work/school") return 3;
  return -1;
}
bool split3(const String& in, String& a, String& b, String& c){
  int p=in.indexOf(' '); if(p<0){a=in;b="";c="";return true;}
  a=in.substring(0,p); a.trim();
  String rest=in.substring(p+1); rest.trim();
  int q=rest.indexOf(' '); if(q<0){b=rest;c="";return true;}
  b=rest.substring(0,q); b.trim(); c=rest.substring(q+1); c.trim(); return true;
}

// --------- core movement: FORWARD ONLY in 90° steps ---------
void spinForwardFor(int i, unsigned long ms){
  int pulse = forwardPulse(i);
  sv[i].writeMicroseconds(pulse);
  Serial.printf("[spin] %-8s forward=%s pulse=%dus dur=%lums\n",
                PERSON[i], FWD_IS_CW[i]?"CW":"CCW", pulse, ms);
  delay(ms);
  hardStop(i);
  delay(120);
}
void moveToQuadrant_ForwardOnly(int i, int targetQ){
  if(i<0||i>=N_SERVOS||targetQ<0||targetQ>3) return;
  int cur = currentQuadrant[i];
  if(cur==targetQ){
    Serial.printf("[servo] %-8s already at Q%d\n", PERSON[i], cur);
    return;
  }
  // steps to advance "forward" (clockwise in your layout) until target
  int steps = (targetQ - cur + 4) % 4;   // 1..3
  unsigned long dur = (unsigned long)steps * MS90_MS[i];
  Serial.printf("[servo] %-8s FORWARD ONLY: cur=%d -> target=%d | steps=%d | ms90=%lu -> dur=%lums | forward=%s\n",
                PERSON[i], cur, targetQ, steps, MS90_MS[i], dur, FWD_IS_CW[i]?"CW":"CCW");
  spinForwardFor(i, dur);
  currentQuadrant[i] = targetQ;
}

// --------- commands ---------
void printStatus(){
  Serial.println(F("[status]"));
  for(int i=0;i<N_SERVOS;++i){
    Serial.printf("  [%d] %-8s pin=%d quad=%d NEU=%d CW=%d CCW=%d ms90=%lu forward=%s\n",
                  i, PERSON[i], SERVO_PINS[i], currentQuadrant[i],
                  NEUTRAL_US[i], CW_US[i], CCW_US[i], MS90_MS[i], FWD_IS_CW[i]?"CW":"CCW");
  }
}
void handleLine(String ln){
  ln.trim(); if(!ln.length()) return;
  String cmd,a,b; split3(ln,cmd,a,b); cmd.toLowerCase();

  if(cmd=="status"){ printStatus(); return; }
  if(cmd=="stop"){ for(int i=0;i<N_SERVOS;++i) hardStop(i); Serial.println(F("[servo] all stop")); return; }
  if(cmd=="neutral"){ int idx=nameToIndex(a); if(idx<0){Serial.println(F("[err] neutral <person|idx>"));return;}
    hardStop(idx); Serial.printf("[servo] %-8s neutral @ %dus\n", PERSON[idx], NEUTRAL_US[idx]); return; }
  if(cmd=="zero"){ int idx=nameToIndex(a); int q=tokenToQuadrant(b);
    if(idx<0||q<0){Serial.println(F("[err] zero <person|idx> <0..3|word>"));return;}
    currentQuadrant[idx]=q; Serial.printf("[servo] %-8s zeroed to Q%d (no move)\n", PERSON[idx], q); return; }
  if(cmd=="dir"){ int idx=nameToIndex(a); String v=b; v.toLowerCase();
    if(idx<0 || (v!="cw" && v!="ccw")){ Serial.println(F("[err] dir <person|idx> <cw|ccw>")); return; }
    FWD_IS_CW[idx] = (v=="cw");
    Serial.printf("[servo] %-8s forward set to %s\n", PERSON[idx], FWD_IS_CW[idx]?"CW":"CCW");
    return; }
  if(cmd=="test_fwd"){ int idx=nameToIndex(a); unsigned long ms=(unsigned long)b.toInt();
    if(idx<0||ms==0){Serial.println(F("[err] test_fwd <person|idx> <ms>"));return;}
    spinForwardFor(idx, ms); return; }
  if(cmd=="set"){ int idx=nameToIndex(a); int q=tokenToQuadrant(b);
    Serial.printf("[cmd] set person='%s' -> idx=%d, state='%s' -> Q=%d\n", a.c_str(), idx, b.c_str(), q);
    if(idx<0||q<0){Serial.println(F("[err] set <person> <0..3|word>"));return;}
    moveToQuadrant_ForwardOnly(idx, q); return; }
  if(cmd=="set_idx"){ bool num=true; for(size_t k=0;k<a.length();++k) if(!isDigit(a[k])){num=false;break;}
    int idx = num ? a.toInt() : -1; int q=tokenToQuadrant(b);
    Serial.printf("[cmd] set_idx i=%d, state='%s' -> Q=%d\n", idx, b.c_str(), q);
    if(idx<0||idx>=N_SERVOS||q<0){Serial.println(F("[err] set_idx <0..3> <0..3|word>"));return;}
    moveToQuadrant_ForwardOnly(idx, q); return; }

  Serial.printf("[err] unknown cmd '%s'\n", cmd.c_str());
}

void setup(){
  Serial.begin(BAUD);
  for(int i=0;i<N_SERVOS;++i){
    sv[i].setPeriodHertz(50);
    sv[i].attach(SERVO_PINS[i], 500, 2500);
    hardStop(i);
  }
  delay(300);
  Serial.println(F("[ready] status | stop | neutral <p|i> | zero <p|i> <q> | dir <p|i> <cw|ccw> | test_fwd <p|i> <ms> | set <p> <state> | set_idx <i> <state>"));
}

void loop(){
  while(Serial.available()){
    char c=Serial.read();
    if(c=='\r') continue;
    if(c=='\n'){ handleLine(line); line=""; }
    else { line+=c; if(line.length()>160) line=""; }
  }
}
