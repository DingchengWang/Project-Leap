#ifndef LEAP_GLASS_BRIDGE_H
#define LEAP_GLASS_BRIDGE_H
#include <stddef.h>
#include <stdint.h>

typedef void (*LeapGlassAction)(const char *action, void *context);
// Appearance callbacks carry UTF-8 JSON with dark, sRGB accent RGBA, and
// reducedMotion. All calls and callbacks run on the main thread. Snapshot
// pointers remain valid until the next callback/snapshot or destruction.
void *leap_appearance_create(LeapGlassAction callback, void *context);
const char *leap_appearance_snapshot(void *handle);
void leap_appearance_destroy(void *handle);
// Publish the same rendered icon to running Dock and existing native sidebars.
// The caller retains PNG bytes; the bridge copies them during the main-thread call.
int leap_application_icon_update(const uint8_t *png, size_t length);

int leap_glass_available(void);
// The opaque handle owns the native control model and NSView; view returns the
// borrowed NSView pointer for Qt. All handle functions run on the main thread.
void *leap_glass_create(LeapGlassAction callback, void *context);
uintptr_t leap_glass_view(void *handle);
int leap_glass_update(void *handle, const char *json);
// Destroy the Qt wrapper before releasing the handle.
void leap_glass_destroy(void *handle);

// Horizontal age selector; the same ownership and controller-confirmation rules.
void *leap_glass_age_create(LeapGlassAction callback, void *context);
uintptr_t leap_glass_age_view(void *handle);
int leap_glass_age_update(void *handle, const char *json);
void leap_glass_age_destroy(void *handle);

// Foundation-only service; may run on a worker thread. Returns zero on success.
// The caller owns the UTF-8 error buffer. Nonzero capacity permits a terminating
// NUL; failure text may be truncated to fit. No shared error state is retained.
int leap_storage_publish_directory(const char *source, const char *destination,
                                   char *error_buffer, size_t error_capacity);
#endif
