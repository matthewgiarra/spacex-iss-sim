/*
 * hackathon_client.h: header-only C client for the Dragon Docking Challenge (C99; Windows, macOS, Linux).
 *
 * Request-reply with your simulator through the challenge website, over HTTPS. It uses the HTTPS library
 * your system already has: WinHTTP on Windows, libcurl on macOS and Linux.
 *
 *     #include "hackathon_client.h"      (include it first, before other headers)
 *
 *     hk_connection *c = hk_connect("https://<site>/ap/<your-key>", 10000);   // autopilot URL, timeout in ms
 *     hk_telemetry t;
 *     if (hk_request_telemetry(c, &t) != 0) { puts(hk_last_error(c)); ... }
 *     double inputs[6] = {0, 0, -2, 0, 1, 0};                                  // x, y, z, roll, pitch, yaw pulses
 *     hk_send_control_inputs(c, inputs, NULL);
 *     hk_close(c);
 *
 * Build: macOS      cc -I include examples/autopilot.c -o autopilot -lcurl -lm
 *        Linux      cc -I include examples/autopilot.c -o autopilot -lcurl -lm   (needs libcurl's dev package)
 *        Windows    gcc -I include examples/autopilot.c -o autopilot.exe -lwinhttp   (MinGW)
 *                   cl /I include examples\autopilot.c                              (Visual Studio)
 *
 * Every request function returns 0 when the website answered (then check the reply's ok field) and -1
 * when the request didn't get through (no network, wrong or expired URL, timeout); hk_last_error() says
 * why. The connection is kept open between requests. Not thread-safe: use one connection per thread.
 * See AUTOPILOT_API.md for every field.
 */
#ifndef HACKATHON_CLIENT_H
#define HACKATHON_CLIENT_H

#if !defined(_WIN32) && !defined(_POSIX_C_SOURCE) && !defined(_GNU_SOURCE)
#define _POSIX_C_SOURCE 200809L /* nanosleep, even with -std=c99 */
#endif

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <winhttp.h>
#ifdef _MSC_VER
#pragma comment(lib, "winhttp.lib")
#endif
#else
#include <curl/curl.h>
#endif

#if defined(_MSC_VER) && !defined(__cplusplus)
#define HK_API static __inline
#else
#define HK_API static inline
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Indexes into hk_telemetry.state. Units: metres, m/s, degrees, deg/s. Position is relative to the docking port. */
enum { HK_X, HK_Y, HK_Z, HK_X_DOT, HK_Y_DOT, HK_Z_DOT, HK_ROLL, HK_PITCH, HK_YAW, HK_ROLL_DOT, HK_PITCH_DOT, HK_YAW_DOT };
/* Indexes into control inputs (thruster pulses; positive increases that velocity or rotation rate). */
enum { HK_IN_X, HK_IN_Y, HK_IN_Z, HK_IN_ROLL, HK_IN_PITCH, HK_IN_YAW };

typedef struct {
    int ok;                     /* 1, or 0 with error set */
    char status[16];            /* "flying", "waiting", "launching", "docked", "failed" or "offline" */
    int docked;                 /* 1 once you've docked */
    char flight_id[40];         /* changes with every flight; "" when there's none */
    long long timestamp_ms;     /* sim time in this flight; unchanged = nothing new yet */
    int has_state;              /* 0 when there's no flight (state is then all zeros) */
    double state[12];           /* x, y, z, x_dot, y_dot, z_dot, roll, pitch, yaw, roll_dot, pitch_dot, yaw_dot */
    double commanded_rates[3];  /* roll, pitch, yaw rates (deg/s) the vehicle is easing toward */
    int rank;                   /* leaderboard place after docking; 0 if none */
    char message[256];          /* why a flight ended, or what's going on */
    char error[256];
} hk_telemetry;

typedef struct {
    int ok;                     /* 1, or 0 with error set (e.g. "no flight in progress") */
    long long timestamp_ms;     /* control: sim time your inputs took effect */
    int pulses[6];              /* control: the whole pulses actually fired on each axis */
    char flight_id[40];         /* launch: the new flight */
    char error[256];
} hk_reply;

typedef struct {
    char url[1024];             /* the request endpoint: <autopilot URL>/api/request */
    char client_id[20];         /* tells the website which requests come from this run of your program */
    int timeout_ms;
    char last_error[512];
    char *buf;                  /* last reply */
    size_t buf_len, buf_cap;
#ifdef _WIN32
    HINTERNET session, connection;
    wchar_t path[1024];
    int secure;
#else
    CURL *curl;
    struct curl_slist *headers;
#endif
} hk_connection;

/* ------------------------------------------------------------------------------------------ helpers */

HK_API void hk_sleep_ms(int ms) {
#ifdef _WIN32
    Sleep((DWORD)ms);
#else
    struct timespec ts;
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (long)(ms % 1000) * 1000000L;
    nanosleep(&ts, NULL);
#endif
}

HK_API int hk__append(hk_connection *c, const char *data, size_t n) {
    if (c->buf_len + n + 1 > c->buf_cap) {
        size_t cap = (c->buf_len + n + 1) * 2;
        char *bigger = (char *)realloc(c->buf, cap);
        if (!bigger) return -1;
        c->buf = bigger;
        c->buf_cap = cap;
    }
    memcpy(c->buf + c->buf_len, data, n);
    c->buf_len += n;
    c->buf[c->buf_len] = '\0';
    return 0;
}

/* ------------------------------------------------------------------------------------------ HTTPS */

#ifdef _WIN32

HK_API const char *hk__winhttp_error(DWORD e) {
    switch (e) {
    case ERROR_WINHTTP_TIMEOUT: return "timed out";
    case ERROR_WINHTTP_NAME_NOT_RESOLVED: return "couldn't find the server (check the URL and your internet connection)";
    case ERROR_WINHTTP_CANNOT_CONNECT: return "couldn't connect to the server";
    case ERROR_WINHTTP_CONNECTION_ERROR: return "the connection was reset";
    case ERROR_WINHTTP_SECURE_FAILURE: return "secure connection (TLS) failed";
    default: return "network error";
    }
}

HK_API int hk__transport_open(hk_connection *c, const char *url) {
    wchar_t wurl[1024], host[256];
    URL_COMPONENTS uc;
    if (!MultiByteToWideChar(CP_UTF8, 0, url, -1, wurl, 1024)) return -1;
    memset(&uc, 0, sizeof uc);
    uc.dwStructSize = sizeof uc;
    uc.dwSchemeLength = (DWORD)-1;
    uc.dwHostNameLength = (DWORD)-1;
    uc.dwUrlPathLength = (DWORD)-1;
    uc.dwExtraInfoLength = (DWORD)-1;
    if (!WinHttpCrackUrl(wurl, 0, 0, &uc) || uc.dwHostNameLength >= 256 || uc.dwUrlPathLength >= 1024) return -1;
    memcpy(host, uc.lpszHostName, uc.dwHostNameLength * sizeof(wchar_t));
    host[uc.dwHostNameLength] = L'\0';
    memcpy(c->path, uc.lpszUrlPath, uc.dwUrlPathLength * sizeof(wchar_t));
    c->path[uc.dwUrlPathLength] = L'\0';
    c->secure = uc.nScheme == INTERNET_SCHEME_HTTPS;
    c->session = WinHttpOpen(L"hackathon_client/3.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!c->session) return -1;
    WinHttpSetTimeouts(c->session, c->timeout_ms, c->timeout_ms, c->timeout_ms, c->timeout_ms);
    c->connection = WinHttpConnect(c->session, host, uc.nPort, 0);
    return c->connection ? 0 : -1;
}

/* POST json; fills c->buf with the body. Returns the HTTP status, or -1 with last_error set. */
HK_API int hk__post(hk_connection *c, const char *json) {
    DWORD len = (DWORD)strlen(json), status = 0, size = sizeof status, avail = 0, got = 0;
    HINTERNET req = WinHttpOpenRequest(c->connection, L"POST", c->path, NULL, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
                                       c->secure ? WINHTTP_FLAG_SECURE : 0);
    c->buf_len = 0;
    if (!req ||
        !WinHttpSendRequest(req, L"Content-Type: application/json\r\n", (DWORD)-1L, (LPVOID)json, len, len, 0) ||
        !WinHttpReceiveResponse(req, NULL) ||
        !WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX, &status, &size, WINHTTP_NO_HEADER_INDEX)) {
        DWORD e = GetLastError();
        snprintf(c->last_error, sizeof c->last_error, "Couldn't reach %s: %s (WinHTTP error %lu)", c->url, hk__winhttp_error(e), (unsigned long)e);
        if (req) WinHttpCloseHandle(req);
        return -1;
    }
    for (;;) {
        char chunk[4096];
        if (!WinHttpQueryDataAvailable(req, &avail) || avail == 0) break;
        if (avail > sizeof chunk) avail = sizeof chunk;
        if (!WinHttpReadData(req, chunk, avail, &got) || got == 0) break;
        if (hk__append(c, chunk, got)) break;
    }
    WinHttpCloseHandle(req);
    return (int)status;
}

HK_API void hk__transport_close(hk_connection *c) {
    if (c->connection) WinHttpCloseHandle(c->connection);
    if (c->session) WinHttpCloseHandle(c->session);
}

#else

HK_API size_t hk__on_data(char *data, size_t size, size_t count, void *user) {
    return hk__append((hk_connection *)user, data, size * count) == 0 ? size * count : 0;
}

HK_API int hk__transport_open(hk_connection *c, const char *url) {
    (void)url;
    if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK || !(c->curl = curl_easy_init())) return -1;
    c->headers = curl_slist_append(NULL, "Content-Type: application/json");
    c->headers = curl_slist_append(c->headers, "Expect:");
    curl_easy_setopt(c->curl, CURLOPT_URL, c->url);
    curl_easy_setopt(c->curl, CURLOPT_HTTPHEADER, c->headers);
    curl_easy_setopt(c->curl, CURLOPT_WRITEFUNCTION, hk__on_data);
    curl_easy_setopt(c->curl, CURLOPT_WRITEDATA, (void *)c);
    curl_easy_setopt(c->curl, CURLOPT_TIMEOUT_MS, (long)c->timeout_ms);
    curl_easy_setopt(c->curl, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(c->curl, CURLOPT_TCP_NODELAY, 1L);
    return 0;
}

/* POST json; fills c->buf with the body. Returns the HTTP status, or -1 with last_error set. */
HK_API int hk__post(hk_connection *c, const char *json) {
    long status = 0;
    CURLcode rc;
    c->buf_len = 0;
    curl_easy_setopt(c->curl, CURLOPT_POSTFIELDS, json);
    curl_easy_setopt(c->curl, CURLOPT_POSTFIELDSIZE, (long)strlen(json));
    rc = curl_easy_perform(c->curl);
    if (rc != CURLE_OK) {
        snprintf(c->last_error, sizeof c->last_error, "Couldn't reach %s: %s", c->url, curl_easy_strerror(rc));
        return -1;
    }
    curl_easy_getinfo(c->curl, CURLINFO_RESPONSE_CODE, &status);
    return (int)status;
}

HK_API void hk__transport_close(hk_connection *c) {
    if (c->curl) curl_easy_cleanup(c->curl);
    if (c->headers) curl_slist_free_all(c->headers);
    curl_global_cleanup();
}

#endif

/* ------------------------------------------------------------------------------------------ JSON replies */

typedef struct { const char *p, *end; } hk__json;

HK_API void hk__ws(hk__json *j) {
    while (j->p < j->end && (*j->p == ' ' || *j->p == '\t' || *j->p == '\n' || *j->p == '\r')) j->p++;
}

HK_API void hk__put(char *out, size_t cap, size_t *n, unsigned long cp) {  /* append a code point as UTF-8 */
    unsigned char b[4];
    int k = 0, i;
    if (cp < 0x80) b[k++] = (unsigned char)cp;
    else if (cp < 0x800) { b[k++] = (unsigned char)(0xC0 | (cp >> 6)); b[k++] = (unsigned char)(0x80 | (cp & 0x3F)); }
    else if (cp < 0x10000) { b[k++] = (unsigned char)(0xE0 | (cp >> 12)); b[k++] = (unsigned char)(0x80 | ((cp >> 6) & 0x3F)); b[k++] = (unsigned char)(0x80 | (cp & 0x3F)); }
    else { b[k++] = (unsigned char)(0xF0 | (cp >> 18)); b[k++] = (unsigned char)(0x80 | ((cp >> 12) & 0x3F)); b[k++] = (unsigned char)(0x80 | ((cp >> 6) & 0x3F)); b[k++] = (unsigned char)(0x80 | (cp & 0x3F)); }
    for (i = 0; i < k; i++)
        if (out && *n + 1 < cap) out[(*n)++] = (char)b[i];
}

HK_API unsigned long hk__hex4(hk__json *j) {
    unsigned long v = 0;
    int i;
    for (i = 0; i < 4 && j->p < j->end; i++, j->p++) {
        char ch = *j->p;
        v = v * 16 + (unsigned long)(ch >= '0' && ch <= '9' ? ch - '0' : ch >= 'a' && ch <= 'f' ? ch - 'a' + 10 : ch >= 'A' && ch <= 'F' ? ch - 'A' + 10 : 0);
    }
    return v;
}

/* Parse a string into out (truncated to cap; out may be NULL to skip). */
HK_API int hk__string(hk__json *j, char *out, size_t cap) {
    size_t n = 0;
    if (j->p >= j->end || *j->p != '"') return -1;
    j->p++;
    while (j->p < j->end && *j->p != '"') {
        char ch = *j->p++;
        if (ch != '\\') { if (out && n + 1 < cap) out[n++] = ch; continue; }
        if (j->p >= j->end) return -1;
        ch = *j->p++;
        switch (ch) {
        case 'n': hk__put(out, cap, &n, '\n'); break;
        case 't': hk__put(out, cap, &n, '\t'); break;
        case 'r': hk__put(out, cap, &n, '\r'); break;
        case 'b': hk__put(out, cap, &n, '\b'); break;
        case 'f': hk__put(out, cap, &n, '\f'); break;
        case 'u': {
            unsigned long cp = hk__hex4(j);
            if (cp >= 0xD800 && cp < 0xDC00 && j->end - j->p >= 6 && j->p[0] == '\\' && j->p[1] == 'u') {
                unsigned long lo;
                j->p += 2;
                lo = hk__hex4(j);
                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
            }
            hk__put(out, cap, &n, cp);
            break;
        }
        default: hk__put(out, cap, &n, (unsigned char)ch); break;  /* \" \\ \/ */
        }
    }
    if (j->p >= j->end) return -1;
    j->p++;
    if (out && cap) out[n] = '\0';
    return 0;
}

HK_API int hk__literal(hk__json *j, const char *word) {
    size_t n = strlen(word);
    if ((size_t)(j->end - j->p) < n || strncmp(j->p, word, n) != 0) return 0;
    j->p += n;
    return 1;
}

HK_API int hk__number(hk__json *j, double *out) {
    char tmp[64], *stop;
    size_t n = 0;
    while (j->p + n < j->end && n < sizeof tmp - 1 && strchr("+-0123456789.eE", j->p[n])) n++;
    if (n == 0) return -1;
    memcpy(tmp, j->p, n);
    tmp[n] = '\0';
    *out = strtod(tmp, &stop);
    j->p += n;
    return stop == tmp ? -1 : 0;
}

HK_API int hk__skip(hk__json *j, int depth) {  /* skip any value */
    double ignored;
    hk__ws(j);
    if (j->p >= j->end || depth > 32) return -1;
    if (*j->p == '"') return hk__string(j, NULL, 0);
    if (*j->p == '{' || *j->p == '[') {
        char close = *j->p == '{' ? '}' : ']';
        j->p++;
        hk__ws(j);
        if (j->p < j->end && *j->p == close) { j->p++; return 0; }
        for (;;) {
            if (close == '}') {
                hk__ws(j);
                if (hk__string(j, NULL, 0)) return -1;
                hk__ws(j);
                if (j->p >= j->end || *j->p++ != ':') return -1;
            }
            if (hk__skip(j, depth + 1)) return -1;
            hk__ws(j);
            if (j->p < j->end && *j->p == ',') { j->p++; continue; }
            if (j->p < j->end && *j->p == close) { j->p++; return 0; }
            return -1;
        }
    }
    if (hk__literal(j, "true") || hk__literal(j, "false") || hk__literal(j, "null")) return 0;
    return hk__number(j, &ignored);
}

/* Parse [n numbers] (or null) into out; returns how many were read, or -1. */
HK_API int hk__numbers(hk__json *j, double *out, int max) {
    int count = 0;
    hk__ws(j);
    if (hk__literal(j, "null")) return 0;
    if (j->p >= j->end || *j->p != '[') return -1;
    j->p++;
    hk__ws(j);
    if (j->p < j->end && *j->p == ']') { j->p++; return 0; }
    for (;;) {
        double v;
        hk__ws(j);
        if (hk__number(j, &v)) return -1;
        if (count < max) out[count] = v;
        count++;
        hk__ws(j);
        if (j->p < j->end && *j->p == ',') { j->p++; continue; }
        if (j->p < j->end && *j->p == ']') { j->p++; return count < max ? count : max; }
        return -1;
    }
}

/* Read a reply object into t and/or r (either may be NULL). */
HK_API int hk__parse(const char *text, size_t len, hk_telemetry *t, hk_reply *r) {
    hk__json j;
    j.p = text;
    j.end = text + len;
    hk__ws(&j);
    if (j.p >= j.end || *j.p++ != '{') return -1;
    hk__ws(&j);
    if (j.p < j.end && *j.p == '}') return 0;
    for (;;) {
        char key[32];
        double num;
        hk__ws(&j);
        if (hk__string(&j, key, sizeof key)) return -1;
        hk__ws(&j);
        if (j.p >= j.end || *j.p++ != ':') return -1;
        hk__ws(&j);
        if (!strcmp(key, "ok") || !strcmp(key, "docked")) {
            int v = hk__literal(&j, "true");
            if (!v && !hk__literal(&j, "false")) return -1;
            if (!strcmp(key, "ok")) { if (t) t->ok = v; if (r) r->ok = v; }
            else if (t) t->docked = v;
        } else if (!strcmp(key, "status") && t && *j.p == '"') {
            if (hk__string(&j, t->status, sizeof t->status)) return -1;
        } else if (!strcmp(key, "flight_id") && *j.p == '"') {
            char id[40];
            if (hk__string(&j, id, sizeof id)) return -1;
            if (t) memcpy(t->flight_id, id, sizeof id);
            if (r) memcpy(r->flight_id, id, sizeof id);
        } else if (!strcmp(key, "message") && t && *j.p == '"') {
            if (hk__string(&j, t->message, sizeof t->message)) return -1;
        } else if (!strcmp(key, "error") && *j.p == '"') {
            char err[256];
            if (hk__string(&j, err, sizeof err)) return -1;
            if (t) memcpy(t->error, err, sizeof err);
            if (r) memcpy(r->error, err, sizeof err);
        } else if (!strcmp(key, "timestamp_ms") && *j.p != 'n') {
            if (hk__number(&j, &num)) return -1;
            if (t) t->timestamp_ms = (long long)num;
            if (r) r->timestamp_ms = (long long)num;
        } else if (!strcmp(key, "rank") && t && *j.p != 'n') {
            if (hk__number(&j, &num)) return -1;
            t->rank = (int)num;
        } else if (!strcmp(key, "state") && t) {
            int n = hk__numbers(&j, t->state, 12);
            if (n < 0) return -1;
            t->has_state = n == 12;
        } else if (!strcmp(key, "commanded_rates") && t) {
            if (hk__numbers(&j, t->commanded_rates, 3) < 0) return -1;
        } else if (!strcmp(key, "pulses") && r) {
            double p[6] = {0, 0, 0, 0, 0, 0};
            int i;
            if (hk__numbers(&j, p, 6) < 0) return -1;
            for (i = 0; i < 6; i++) r->pulses[i] = (int)p[i];
        } else if (hk__skip(&j, 0)) {
            return -1;
        }
        hk__ws(&j);
        if (j.p < j.end && *j.p == ',') { j.p++; continue; }
        if (j.p < j.end && *j.p == '}') return 0;
        return -1;
    }
}

/* ------------------------------------------------------------------------------------------ public API */

/* Connect to your simulator. url is your autopilot URL from the Autopilot page (https://<site>/ap/<your-key>).
 * Returns NULL for a URL that isn't one, or if the system's HTTPS library couldn't start. */
HK_API hk_connection *hk_connect(const char *url, int timeout_ms) {
    hk_connection *c;
    size_t n;
    unsigned long seed;
    int i;
    if (!url || (strncmp(url, "https://", 8) != 0 && strncmp(url, "http://", 7) != 0) || !strstr(url, "/ap/")) return NULL;
    n = strlen(url);
    while (n > 0 && url[n - 1] == '/') n--;
    if (n + sizeof "/api/request" > 1024) return NULL;
    c = (hk_connection *)calloc(1, sizeof *c);
    if (!c) return NULL;
    memcpy(c->url, url, n);
    memcpy(c->url + n, "/api/request", sizeof "/api/request");
    c->timeout_ms = timeout_ms > 0 ? timeout_ms : 10000;
    seed = (unsigned long)time(NULL) ^ (unsigned long)clock() ^ (unsigned long)(size_t)c;
    for (i = 0; i < 16; i++) {
        seed = seed * 1103515245UL + 12345UL;
        c->client_id[i] = "0123456789abcdef"[(seed >> 16) & 15];
    }
    if (hk__transport_open(c, c->url) != 0) {
        hk__transport_close(c);
        free(c);
        return NULL;
    }
    return c;
}

/* Send one raw JSON request object and get the raw JSON reply (valid until the next request).
 * The client id is added for you. Returns 0, or -1 with hk_last_error() set. */
HK_API int hk_request(hk_connection *c, const char *json, const char **reply, size_t *reply_len) {
    char body[1024];
    size_t len = strlen(json);
    int status;
    if (len < 2 || len + 64 > sizeof body || json[len - 1] != '}') {
        snprintf(c->last_error, sizeof c->last_error, "requests must be a JSON object under 900 bytes");
        return -1;
    }
    snprintf(body, sizeof body, "%.*s,\"client\":\"%s\"}", (int)(len - 1), json, c->client_id);
    status = hk__post(c, body);
    if (status < 0) return -1;
    if (status != 200) {
        hk__json j;
        char err[256] = "";
        j.p = c->buf ? c->buf : "";
        j.end = j.p + c->buf_len;
        if (status == 404) snprintf(err, sizeof err, "unknown or expired autopilot URL; press GET AUTOPILOT LINK on the Autopilot page for a new one");
        else if (c->buf_len && (j.p = strstr(j.p, "\"error\"")) != NULL && (j.p = strchr(j.p + 7, '"')) != NULL) hk__string(&j, err, sizeof err);
        snprintf(c->last_error, sizeof c->last_error, "%s (HTTP %d from %s)", err[0] ? err : "unexpected reply", status, c->url);
        return -1;
    }
    *reply = c->buf ? c->buf : "";
    if (reply_len) *reply_len = c->buf_len;
    return 0;
}

HK_API int hk__call(hk_connection *c, const char *json, hk_telemetry *t, hk_reply *r) {
    const char *text;
    size_t len;
    int rc;
    if (t) memset(t, 0, sizeof *t);
    if (r) memset(r, 0, sizeof *r);
    rc = hk_request(c, json, &text, &len);
    if (rc == 0 && hk__parse(text, len, t, r) != 0) {
        snprintf(c->last_error, sizeof c->last_error, "the website sent a reply this client can't read");
        rc = -1;
    }
    if (rc != 0) {
        if (t) { t->ok = 0; snprintf(t->error, sizeof t->error, "%s", c->last_error); }
        if (r) { r->ok = 0; snprintf(r->error, sizeof r->error, "%s", c->last_error); }
    }
    return rc;
}

/* The latest telemetry. It never predates your last control inputs taking effect. */
HK_API int hk_request_telemetry(hk_connection *c, hk_telemetry *t) {
    return hk__call(c, "{\"request\":\"telemetry\"}", t, NULL);
}

/* Fire thrusters: inputs = x, y, z, roll, pitch, yaw pulses (rounded; limited to +/-50). reply may be NULL. */
HK_API int hk_send_control_inputs(hk_connection *c, const double inputs[6], hk_reply *reply) {
    char json[256];
    hk_reply local;
    int i;
    for (i = 0; i < 6; i++)
        if (!(inputs[i] == inputs[i]) || inputs[i] > 1e9 || inputs[i] < -1e9) {  /* NaN or absurd */
            snprintf(c->last_error, sizeof c->last_error, "control inputs must be 6 finite numbers");
            if (reply) { memset(reply, 0, sizeof *reply); snprintf(reply->error, sizeof reply->error, "%s", c->last_error); }
            return -1;
        }
    snprintf(json, sizeof json, "{\"request\":\"control\",\"inputs\":[%.6g,%.6g,%.6g,%.6g,%.6g,%.6g]}",
             inputs[0], inputs[1], inputs[2], inputs[3], inputs[4], inputs[5]);
    return hk__call(c, json, NULL, reply ? reply : &local);
}

/* Start a flight, as the LAUNCH button does. The Dragon takes a few seconds to arrive. */
HK_API int hk_launch(hk_connection *c, hk_reply *reply) {
    hk_reply local;
    return hk__call(c, "{\"request\":\"launch\"}", NULL, reply ? reply : &local);
}

/* End the current flight (it counts as a failed attempt). */
HK_API int hk_abort(hk_connection *c, const char *reason, hk_reply *reply) {
    char json[320];
    size_t n = 0;
    hk_reply local;
    n += (size_t)snprintf(json, sizeof json, "{\"request\":\"abort\",\"reason\":\"");
    for (; reason && *reason && n < sizeof json - 4; reason++)
        if (*reason != '"' && *reason != '\\' && (unsigned char)*reason >= 0x20) json[n++] = *reason;
    snprintf(json + n, sizeof json - n, "\"}");
    return hk__call(c, json, NULL, reply ? reply : &local);
}

HK_API const char *hk_last_error(const hk_connection *c) { return c->last_error; }

HK_API void hk_close(hk_connection *c) {
    if (!c) return;
    hk__transport_close(c);
    free(c->buf);
    free(c);
}

#ifdef __cplusplus
}
#endif
#endif /* HACKATHON_CLIENT_H */
