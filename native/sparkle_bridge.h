#ifndef LEAP_SPARKLE_BRIDGE_H
#define LEAP_SPARKLE_BRIDGE_H
#ifdef __cplusplus
extern "C" {
#endif
/* All calls run on the application's main thread. */
int leap_sparkle_start(void);
/* Inspect update information; this never requests installation. */
int leap_sparkle_check(void);
/* Execute precisely the candidate returned by the preceding completed check. */
int leap_sparkle_install(const char *expected_build, const char *expected_feed, const char *expected_host_build);
/* Downloading may continue while busy; installation and relaunch wait for idle. */
void leap_sparkle_set_installation_busy(int busy);
/* UTF-8 JSON remains valid until the next state call. */
const char *leap_sparkle_state_json(void);
#ifdef __cplusplus
}
#endif
#endif
