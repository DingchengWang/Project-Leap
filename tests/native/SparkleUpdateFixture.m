#import <Cocoa/Cocoa.h>
#import "sparkle_bridge.h"

static NSString *reportPath;
static void record(NSDictionary *value) {
    NSData *json = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    NSFileHandle *file = [NSFileHandle fileHandleForWritingAtPath:reportPath];
    [file seekToEndOfFile];
    [file writeData:json];
    [file writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
    [file closeFile];
}
int main(void) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        NSBundle *bundle = NSBundle.mainBundle;
        reportPath = [bundle objectForInfoDictionaryKey:@"FixtureReportPath"];
        NSString *build = [bundle objectForInfoDictionaryKey:@"CFBundleVersion"];
        if ([build isEqualToString:@"2"]) {
            record(@{@"event": @"relaunched", @"build": build});
            return 0;
        }
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        record(@{@"event": @"launched", @"build": build, @"start": @(leap_sparkle_start())});
        leap_sparkle_check();
        __block BOOL installStarted = NO;
        __block BOOL released = NO;
        __block NSString *lastStatus = @"";
        __block NSTimeInterval readyAt = 0;
        NSTimeInterval startedAt = NSDate.timeIntervalSinceReferenceDate;
        [NSTimer scheduledTimerWithTimeInterval:0.05 repeats:YES block:^(NSTimer *timer) {
            NSData *data = [[NSString stringWithUTF8String:leap_sparkle_state_json()] dataUsingEncoding:NSUTF8StringEncoding];
            NSDictionary *state = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
            NSString *current = state[@"status"];
            if (![current isEqualToString:lastStatus]) {
                record(state);
                lastStatus = current;
            }
            if ([current isEqualToString:@"available"] && ![state[@"session_in_progress"] boolValue] && !installStarted) {
                installStarted = YES;
                leap_sparkle_set_installation_busy(1);
                NSString *scenario = [bundle objectForInfoDictionaryKey:@"FixtureScenario"];
                NSString *target = [scenario isEqualToString:@"plan_mismatch"] ? @"3" : state[@"available_build"];
                NSString *feed = [scenario isEqualToString:@"feed_mismatch"] ? @"https://example.invalid/other.xml" : state[@"feed_url"];
                NSString *host = [scenario isEqualToString:@"host_mismatch"] ? @"0" : state[@"host_build"];
                int result = leap_sparkle_install(target.UTF8String, feed.UTF8String, host.UTF8String);
                record(@{@"event": @"install_requested", @"accepted": @(result)});
            }
            if ([current isEqualToString:@"ready_to_install"] && !released) {
                if (readyAt == 0) readyAt = NSDate.timeIntervalSinceReferenceDate;
                if (NSDate.timeIntervalSinceReferenceDate - readyAt >= 1.0) {
                    record(@{@"event": @"idle_released", @"waited_seconds": @(NSDate.timeIntervalSinceReferenceDate - readyAt)});
                    released = YES;
                    leap_sparkle_set_installation_busy(0);
                }
            }
            if ([current isEqualToString:@"failed"] || [current isEqualToString:@"no_update"]) {
                [timer invalidate];
                [app terminate:nil];
            }
            if (NSDate.timeIntervalSinceReferenceDate - startedAt > 60) {
                record(@{@"event": @"timeout"});
                [timer invalidate];
                [app terminate:nil];
            }
        }];
        [app run];
    }
    return 0;
}
