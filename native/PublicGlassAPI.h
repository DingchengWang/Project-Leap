#import <AppKit/AppKit.h>

// Public macOS 27 API, declared here because the local compiler SDK is 26.2.
// https://developer.apple.com/documentation/appkit/nsglasseffectview/effectisinteractive
@interface NSGlassEffectView (PublicMacOS27API)
@property BOOL effectIsInteractive API_AVAILABLE(macos(27.0));
@end
