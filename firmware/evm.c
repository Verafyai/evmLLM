// evm.c: a small EVM interpreter for the Qwenputer RV32IM machine (freestanding, no libc).
// 256-bit words are 8 x 32-bit limbs, least significant limb first.
// The host writes the job (code, calldata, preset storage, flags) into `job` before the run;
// the result is printed through putchar ecalls, one byte per trace token.
#include <stdint.h>
#include "rt.h"

#define CODE_MAX 3072
#define CD_MAX 256
#define MEM_MAX 2048
#define STK_MAX 96
#define SLOTS 48

typedef struct { uint32_t w[8]; } u256;

struct job_t {
  uint32_t magic, code_len, cd_len, flags, gas_limit, n_pre;
  u256 caller, callvalue, address;
  uint8_t code[CODE_MAX];
  uint8_t cd[CD_MAX];
  u256 pre_key[8], pre_val[8];
};
struct job_t job __attribute__((section(".data"), used)) = {0x45564d31};

static u256 stk[STK_MAX];
static uint8_t mem[MEM_MAX];
static uint8_t jd[CODE_MAX / 8];
static u256 skey[SLOTS], sval[SLOTS], sorig[SLOTS];
static uint8_t swarm[SLOTS], sdirty[SLOTS];
static int nslots;
static uint32_t gas, msize_words;

static void putc_(int c) { rv_putchar((char)c); }
static void puthex4(uint32_t v) { putc_("0123456789abcdef"[v & 15]); }
static void putnum(uint32_t v) {  // hex, no leading zeros
  int started = 0;
  for (int i = 28; i >= 0; i -= 4) { uint32_t d = (v >> i) & 15; if (d || started || i == 0) { puthex4(d); started = 1; } }
}
static void put256(const u256 *a) {
  int started = 0;
  for (int i = 7; i >= 0; i--) for (int s = 28; s >= 0; s -= 4) {
    uint32_t d = (a->w[i] >> s) & 15;
    if (d || started || (i == 0 && s == 0)) { puthex4(d); started = 1; }
  }
}
static void exit_(int c) { for (;;) rv_ecall2(93, c, 0, 0); }

static int iszero(const u256 *a) { for (int i = 0; i < 8; i++) if (a->w[i]) return 0; return 1; }
static int eq(const u256 *a, const u256 *b) { for (int i = 0; i < 8; i++) if (a->w[i] != b->w[i]) return 0; return 1; }
static int ult(const u256 *a, const u256 *b) {
  for (int i = 7; i >= 0; i--) if (a->w[i] != b->w[i]) return a->w[i] < b->w[i];
  return 0;
}
static void set32(u256 *a, uint32_t v) { a->w[0] = v; for (int i = 1; i < 8; i++) a->w[i] = 0; }
static int fits32(const u256 *a) { for (int i = 1; i < 8; i++) if (a->w[i]) return 0; return 1; }
static void add(u256 *r, const u256 *a, const u256 *b) {
  uint32_t c = 0;
  for (int i = 0; i < 8; i++) { uint32_t s = a->w[i] + b->w[i]; uint32_t c1 = s < a->w[i]; uint32_t s2 = s + c; c = c1 | (s2 < s); r->w[i] = s2; }
}
static void sub(u256 *r, const u256 *a, const u256 *b) {
  uint32_t br = 0;
  for (int i = 0; i < 8; i++) { uint32_t d = a->w[i] - b->w[i]; uint32_t b1 = a->w[i] < b->w[i]; uint32_t d2 = d - br; br = b1 | (d < br); r->w[i] = d2; }
}
static void mul(u256 *r, const u256 *a, const u256 *b) {
  uint32_t t[8] = {0};
  for (int i = 0; i < 8; i++) {
    if (!a->w[i]) continue;
    uint64_t c = 0;
    for (int j = 0; i + j < 8; j++) {
      uint64_t p = (uint64_t)a->w[i] * b->w[j] + t[i + j] + c;
      t[i + j] = (uint32_t)p; c = p >> 32;
    }
  }
  for (int i = 0; i < 8; i++) r->w[i] = t[i];
}
static int bitlen(const u256 *a) {
  for (int i = 7; i >= 0; i--) if (a->w[i]) return i * 32 + 32 - __builtin_clz(a->w[i]);
  return 0;
}
static void shl1(u256 *a) { for (int i = 7; i > 0; i--) a->w[i] = (a->w[i] << 1) | (a->w[i - 1] >> 31); a->w[0] <<= 1; }
static void divmod(u256 *q, u256 *r, const u256 *a, const u256 *b) {
  if (iszero(b)) { set32(q, 0); set32(r, 0); return; }
  if (fits32(a) && fits32(b)) { uint32_t x = a->w[0], y = b->w[0]; set32(q, x / y); set32(r, x % y); return; }
  if (fits32(b)) {  // one-limb divisor: schoolbook with 64/32 steps
    uint32_t y = b->w[0], rem = 0; u256 Q;
    for (int i = 7; i >= 0; i--) {
      if (!rem && !a->w[i]) { Q.w[i] = 0; continue; }
      uint64_t cur = ((uint64_t)rem << 32) | a->w[i];
      uint32_t qq = (uint32_t)(cur / y); Q.w[i] = qq; rem = (uint32_t)(cur - (uint64_t)qq * y);
    }
    *q = Q; set32(r, rem); return;
  }
  if (ult(a, b)) { set32(q, 0); *r = *a; return; }
  // Knuth algorithm D on 32-bit limbs (after Hacker's Delight divmnu)
  int m = 8, nn = 8, s = 0;
  while (m > 0 && !a->w[m - 1]) m--;
  while (!b->w[nn - 1]) nn--;
  { uint32_t top = b->w[nn - 1]; while (!(top & 0x80000000u)) { top <<= 1; s++; } }
  uint32_t vn[8], un[9]; u256 Q, R; set32(&Q, 0); set32(&R, 0);
  for (int i = nn - 1; i > 0; i--) vn[i] = (b->w[i] << s) | (uint32_t)((uint64_t)b->w[i - 1] >> (32 - s));
  vn[0] = b->w[0] << s;
  un[m] = (uint32_t)((uint64_t)a->w[m - 1] >> (32 - s));
  for (int i = m - 1; i > 0; i--) un[i] = (a->w[i] << s) | (uint32_t)((uint64_t)a->w[i - 1] >> (32 - s));
  un[0] = a->w[0] << s;
  for (int j = m - nn; j >= 0; j--) {
    uint64_t num = ((uint64_t)un[j + nn] << 32) | un[j + nn - 1];
    uint64_t qhat = num / vn[nn - 1], rhat = num - qhat * vn[nn - 1];
    while (qhat >> 32 || qhat * vn[nn - 2] > ((rhat << 32) | un[j + nn - 2])) {
      qhat--; rhat += vn[nn - 1];
      if (rhat >> 32) break;
    }
    int64_t t, k = 0;
    for (int i = 0; i < nn; i++) {
      uint64_t p = qhat * vn[i];
      t = (int64_t)un[i + j] - k - (int64_t)(p & 0xffffffffu);
      un[i + j] = (uint32_t)t;
      k = (int64_t)(p >> 32) - (t >> 32);
    }
    t = (int64_t)un[j + nn] - k; un[j + nn] = (uint32_t)t;
    Q.w[j] = (uint32_t)qhat;
    if (t < 0) {
      Q.w[j]--; uint64_t c = 0;
      for (int i = 0; i < nn; i++) { uint64_t u = (uint64_t)un[i + j] + vn[i] + c; un[i + j] = (uint32_t)u; c = u >> 32; }
      un[j + nn] += (uint32_t)c;
    }
  }
  for (int i = 0; i < nn - 1; i++) R.w[i] = (un[i] >> s) | (uint32_t)((uint64_t)un[i + 1] << (32 - s));
  R.w[nn - 1] = un[nn - 1] >> s;
  *q = Q; *r = R;
}
static void shl(u256 *r, const u256 *a, uint32_t n) {
  u256 t; set32(&t, 0);
  if (n < 256) { int L = n >> 5, s = n & 31; for (int i = 7; i >= L; i--) { uint32_t v = a->w[i - L] << s; if (s && i - L - 1 >= 0) v |= a->w[i - L - 1] >> (32 - s); t.w[i] = v; } }
  *r = t;
}
static void shr(u256 *r, const u256 *a, uint32_t n, uint32_t fill) {
  u256 t; for (int i = 0; i < 8; i++) t.w[i] = fill;
  if (n < 256) {
    int L = n >> 5, s = n & 31;
    for (int i = 0; i < 8; i++) {
      uint32_t lo = (i + L < 8) ? a->w[i + L] : fill, hi = (i + L + 1 < 8) ? a->w[i + L + 1] : fill;
      t.w[i] = s ? (lo >> s) | (hi << (32 - s)) : lo;
    }
  }
  *r = t;
}
static int sign(const u256 *a) { return a->w[7] >> 31; }
static int slt(const u256 *a, const u256 *b) { int sa = sign(a), sb = sign(b); if (sa != sb) return sa; return ult(a, b); }
static void from_be(u256 *r, const uint8_t *p, int n) {  // n big-endian bytes, right-aligned
  set32(r, 0);
  int li = 0;
  for (int e = n; e > 0; e -= 4, li++) {  // limb li holds bytes p[e-4 .. e-1]
    uint32_t v = p[e - 1];
    if (e >= 2) v |= (uint32_t)p[e - 2] << 8;
    if (e >= 3) v |= (uint32_t)p[e - 3] << 16;
    if (e >= 4) v |= (uint32_t)p[e - 4] << 24;
    r->w[li] = v;
  }
}
static void to_be(uint8_t *p, const u256 *a) { for (int k = 0; k < 32; k++) { int bi = 31 - k; p[k] = a->w[bi >> 2] >> ((bi & 3) * 8); } }

// status: 0 STOP/RETURN, 1 REVERT, 2 out of gas, 3 invalid opcode, 4 stack, 5 bad jump, 6 limit (memory/storage of this build)
static uint32_t ret_off, ret_len;
static void finish(int st) {
  uint32_t used = job.gas_limit - gas;
  if (st >= 2) used = job.gas_limit;
  putc_('s'); putnum(st); putc_(' '); putc_('g'); putnum(used);
  if (st <= 1) { putc_(' '); putc_('r');
    for (uint32_t i = 0; i < ret_len; i++) { puthex4(mem[ret_off + i] >> 4); puthex4(mem[ret_off + i]); } }
  putc_('\n');
  if (st == 0) for (int i = 0; i < nslots; i++) if (sdirty[i] && !eq(&sval[i], &sorig[i])) {
    putc_('k'); put256(&skey[i]); putc_('='); put256(&sval[i]); putc_('\n'); }
  exit_(0);
}
#define FAIL(s) do { finish(s); } while (0)
static void use(uint32_t g) { if (g > gas) { gas = 0; FAIL(2); } gas -= g; }
static uint32_t memcost(uint32_t w) { return 3 * w + (w * w) / 512; }
static void expand(const u256 *off, const u256 *len) {  // charge + bound memory [off, off+len)
  if (iszero(len)) return;
  if (!fits32(off) || !fits32(len) || off->w[0] > MEM_MAX || len->w[0] > MEM_MAX || off->w[0] + len->w[0] > MEM_MAX) {
    if (!fits32(off) || !fits32(len) || off->w[0] + len->w[0] > 0x100000) FAIL(2);  // unpayable
    FAIL(6);
  }
  uint32_t w = (off->w[0] + len->w[0] + 31) / 32;
  if (w > msize_words) { use(memcost(w) - memcost(msize_words)); msize_words = w; }
}
static int slot(const u256 *k) {
  for (int i = 0; i < nslots; i++) if (eq(&skey[i], k)) return i;
  if (nslots == SLOTS) FAIL(6);
  int i = nslots++; skey[i] = *k; set32(&sval[i], 0); set32(&sorig[i], 0); swarm[i] = 0; sdirty[i] = 0;
  return i;
}

int main(void) {
  uint32_t n = job.code_len, sp = 0, pc = 0;
  const uint8_t *code = job.code;
  gas = job.gas_limit;
  for (uint32_t i = 0; i < job.n_pre; i++) { int s = slot((const u256 *)&job.pre_key[i]); sval[s] = *(const u256 *)&job.pre_val[i]; sorig[s] = sval[s]; }
  for (uint32_t i = 0; i < n; i++) { uint8_t op = code[i]; if (op == 0x5b) jd[i >> 3] |= 1 << (i & 7); else if (op >= 0x60 && op <= 0x7f) i += op - 0x5f; }
  int trace = job.flags & 1;
  ret_off = ret_len = 0;
#define NEED(k) do { if (sp < (k)) FAIL(4); } while (0)
#define ROOM(k) do { if (sp + (k) > STK_MAX) FAIL(sp + (k) > 1024 ? 4 : 6); } while (0)
#define T stk[sp - 1]
#define S2 stk[sp - 2]
  for (;;) {
    uint8_t op = pc < n ? code[pc] : 0;
    if (trace) { puthex4(op >> 4); puthex4(op); }
    pc++;
    if (op >= 0x60 && op <= 0x7f) {  // PUSH1..32
      int k = op - 0x5f; use(3); ROOM(1);
      if (pc + k <= n) from_be(&stk[sp++], code + pc, k);
      else { uint8_t buf[32]; for (int j = 0; j < k; j++) buf[j] = (pc + j < n) ? code[pc + j] : 0; from_be(&stk[sp++], buf, k); }
      pc += k; continue;
    }
    if (op >= 0x80 && op <= 0x8f) { int k = op - 0x7f; use(3); NEED(k); ROOM(1); stk[sp] = stk[sp - k]; sp++; continue; }
    if (op >= 0x90 && op <= 0x9f) { int k = op - 0x8f; use(3); NEED(k + 1); u256 t = T; T = stk[sp - 1 - k]; stk[sp - 1 - k] = t; continue; }
    switch (op) {
    case 0x00: finish(0);
    case 0x01: use(3); NEED(2); add(&S2, &T, &S2); sp--; break;
    case 0x02: use(5); NEED(2); mul(&S2, &T, &S2); sp--; break;
    case 0x03: use(3); NEED(2); sub(&S2, &T, &S2); sp--; break;
    case 0x04: case 0x06: { use(5); NEED(2); u256 q, r; divmod(&q, &r, &T, &S2); S2 = op == 4 ? q : r; sp--; break; }
    case 0x0a: { NEED(2); u256 b = T, e = S2, r; int bl = (bitlen(&e) + 7) / 8; use(10 + 50 * bl); set32(&r, 1);
      for (int i = bitlen(&e) - 1; i >= 0; i--) { mul(&r, &r, &r); if ((e.w[i >> 5] >> (i & 31)) & 1) mul(&r, &r, &b); }
      S2 = r; sp--; break; }
    case 0x10: use(3); NEED(2); set32(&S2, ult(&T, &S2)); sp--; break;
    case 0x11: use(3); NEED(2); set32(&S2, ult(&S2, &T)); sp--; break;
    case 0x12: use(3); NEED(2); set32(&S2, slt(&T, &S2)); sp--; break;
    case 0x13: use(3); NEED(2); set32(&S2, slt(&S2, &T)); sp--; break;
    case 0x14: use(3); NEED(2); set32(&S2, eq(&T, &S2)); sp--; break;
    case 0x15: use(3); NEED(1); set32(&T, iszero(&T)); break;
    case 0x16: use(3); NEED(2); for (int i = 0; i < 8; i++) S2.w[i] &= T.w[i]; sp--; break;
    case 0x17: use(3); NEED(2); for (int i = 0; i < 8; i++) S2.w[i] |= T.w[i]; sp--; break;
    case 0x18: use(3); NEED(2); for (int i = 0; i < 8; i++) S2.w[i] ^= T.w[i]; sp--; break;
    case 0x19: use(3); NEED(1); for (int i = 0; i < 8; i++) T.w[i] = ~T.w[i]; break;
    case 0x1a: { use(3); NEED(2); uint32_t v = 0; if (fits32(&T) && T.w[0] < 32) { int bi = 31 - T.w[0]; v = (S2.w[bi >> 2] >> ((bi & 3) * 8)) & 255; } set32(&S2, v); sp--; break; }
    case 0x1b: use(3); NEED(2); shl(&S2, &S2, fits32(&T) ? T.w[0] : 256); sp--; break;
    case 0x1c: use(3); NEED(2); shr(&S2, &S2, fits32(&T) ? T.w[0] : 256, 0); sp--; break;
    case 0x1d: use(3); NEED(2); shr(&S2, &S2, fits32(&T) ? T.w[0] : 256, sign(&S2) ? 0xffffffffu : 0); sp--; break;
    case 0x30: use(2); ROOM(1); stk[sp++] = *(const u256 *)&job.address; break;
    case 0x33: use(2); ROOM(1); stk[sp++] = *(const u256 *)&job.caller; break;
    case 0x34: use(2); ROOM(1); stk[sp++] = *(const u256 *)&job.callvalue; break;
    case 0x35: { use(3); NEED(1); uint32_t o = fits32(&T) ? T.w[0] : 0xffffffffu;
      if (o <= CD_MAX && o + 32 <= job.cd_len) { from_be(&T, job.cd + o, 32); break; }
      uint8_t buf[32]; for (int j = 0; j < 32; j++) buf[j] = (o < job.cd_len && j < (int)(job.cd_len - o)) ? job.cd[o + j] : 0;
      from_be(&T, buf, 32); break; }
    case 0x36: use(2); ROOM(1); set32(&stk[sp++], job.cd_len); break;
    case 0x37: case 0x39: { NEED(3); u256 d = T, s = S2, l = stk[sp - 3]; sp -= 3;
      if (!fits32(&l)) FAIL(2); use(3 + 3 * ((l.w[0] + 31) / 32)); expand(&d, &l);
      uint32_t src = fits32(&s) ? s.w[0] : 0xffffffffu, lim = op == 0x37 ? job.cd_len : n;
      const uint8_t *b = op == 0x37 ? job.cd : code;
      for (uint32_t j = 0; j < l.w[0]; j++) mem[d.w[0] + j] = (src < lim && j < lim - src) ? b[src + j] : 0;
      break; }
    case 0x38: use(2); ROOM(1); set32(&stk[sp++], n); break;
    case 0x3d: use(2); ROOM(1); set32(&stk[sp++], 0); break;
    case 0x50: use(2); NEED(1); sp--; break;
    case 0x51: { use(3); NEED(1); u256 l; set32(&l, 32); expand(&T, &l); from_be(&T, mem + T.w[0], 32); break; }
    case 0x52: { use(3); NEED(2); u256 l; set32(&l, 32); expand(&T, &l); to_be(mem + T.w[0], &S2); sp -= 2; break; }
    case 0x53: { use(3); NEED(2); u256 l; set32(&l, 1); expand(&T, &l); mem[T.w[0]] = S2.w[0]; sp -= 2; break; }
    case 0x54: { NEED(1); int s = slot(&T); use(swarm[s] ? 100 : 2100); swarm[s] = 1; T = sval[s]; break; }
    case 0x55: { NEED(2); if (gas <= 2300) FAIL(2); int s = slot(&T); uint32_t c = swarm[s] ? 0 : 2100; swarm[s] = 1;
      if (eq(&sval[s], &S2)) c += 100; else if (eq(&sorig[s], &sval[s])) c += iszero(&sorig[s]) ? 20000 : 2900; else c += 100;
      use(c); sval[s] = S2; sdirty[s] = 1; sp -= 2; break; }
    case 0x56: use(8); NEED(1); if (!fits32(&T) || T.w[0] >= n || !((jd[T.w[0] >> 3] >> (T.w[0] & 7)) & 1)) FAIL(5); pc = T.w[0]; sp--; break;
    case 0x57: use(10); NEED(2); if (!iszero(&S2)) { if (!fits32(&T) || T.w[0] >= n || !((jd[T.w[0] >> 3] >> (T.w[0] & 7)) & 1)) FAIL(5); pc = T.w[0]; } sp -= 2; break;
    case 0x58: use(2); ROOM(1); set32(&stk[sp++], pc - 1); break;
    case 0x59: use(2); ROOM(1); set32(&stk[sp++], msize_words * 32); break;
    case 0x5a: use(2); ROOM(1); set32(&stk[sp++], gas); break;
    case 0x5b: use(1); break;
    case 0x5f: use(2); ROOM(1); set32(&stk[sp++], 0); break;
    case 0xf3: case 0xfd: NEED(2); expand(&T, &S2); ret_off = fits32(&S2) && S2.w[0] ? T.w[0] : 0; ret_len = S2.w[0]; finish(op == 0xf3 ? 0 : 1);
    default: FAIL(3);
    }
  }
}
