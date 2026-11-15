// Sparkle validates and installs; the application's update page owns the UI.
#import <Cocoa/Cocoa.h>
#import <Sparkle/Sparkle.h>
#import "sparkle_bridge.h"

static SPUUpdater *updater;
static NSString *status = @"not_started";
static NSString *message = @"更新服务尚未启动。";
static NSString *availableVersion;
static NSString *availableBuild;
static NSString *hostBuild;
static NSString *configuredFeed;
static NSString *expectedBuild;
static NSInteger lastErrorCode;
static BOOL installationBusy;
static BOOL checkRequested;
static uint64_t downloadedBytes;
static uint64_t expectedBytes;
static double extractionProgress;
static char *serializedBytes;

static void setState(NSString *value, NSString *detail) {
    status = value;
    message = detail ?: @"";
}
static NSError *planError(NSString *detail) {
    return [NSError errorWithDomain:@"org.projectleap.update" code:1
                           userInfo:@{NSLocalizedDescriptionKey: detail}];
}
static void fail(NSError *error) {
    lastErrorCode = error.code;
    setState(@"failed", error.localizedDescription);
}

@interface LeapUpdateDriver : NSObject <SPUUserDriver>
@property(copy) void (^pendingChoice)(SPUUserUpdateChoice);
@property(copy) void (^pendingRelaunch)(void);
- (void)resumeInstallation;
@end

static LeapUpdateDriver *driver;

@implementation LeapUpdateDriver
- (void)showUpdatePermissionRequest:(SPUUpdatePermissionRequest *)request reply:(void (^)(SUUpdatePermissionResponse *))reply {
    // Project Leap's unified update action owns checks, including their timing.
    reply([[SUUpdatePermissionResponse alloc] initWithAutomaticUpdateChecks:NO sendSystemProfile:NO]);
}
- (void)showUserInitiatedUpdateCheckWithCancellation:(void (^)(void))cancellation {
    setState(@"checking", @"正在检查已选定的应用更新…");
}
- (void)showUpdateFoundWithAppcastItem:(SUAppcastItem *)item state:(SPUUserUpdateState *)state reply:(void (^)(SPUUserUpdateChoice))reply {
    if (expectedBuild.length == 0 || ![item.versionString isEqualToString:expectedBuild] || item.informationOnlyUpdate) {
        fail(planError(@"应用更新与本次已确认的更新计划不一致。请重新检查更新。"));
        reply(SPUUserUpdateChoiceDismiss);
        return;
    }
    // In this stage Install can immediately terminate the app; honor idle first.
    if (state.stage == SPUUserUpdateStageInstalling && installationBusy) {
        self.pendingChoice = reply;
        setState(@"ready_to_install", @"更新已准备好，等待当前任务结束后安装。");
    } else {
        reply(SPUUserUpdateChoiceInstall);
    }
}
- (void)showUpdateReleaseNotesWithDownloadData:(SPUDownloadData *)downloadData {}
- (void)showUpdateReleaseNotesFailedToDownloadWithError:(NSError *)error {}
- (void)showUpdateNotFoundWithError:(NSError *)error acknowledgement:(void (^)(void))acknowledgement {
    if (expectedBuild.length != 0) fail(planError(@"已选定的应用更新已不再可用。请重新检查更新。"));
    else setState(@"no_update", @"当前应用已是最新可用版本。");
    acknowledgement();
}
- (void)showUpdaterError:(NSError *)error acknowledgement:(void (^)(void))acknowledgement {
    fail(error);
    acknowledgement();
}
- (void)showDownloadInitiatedWithCancellation:(void (^)(void))cancellation {
    downloadedBytes = expectedBytes = 0;
    setState(@"downloading", @"正在下载应用更新…");
}
- (void)showDownloadDidReceiveExpectedContentLength:(uint64_t)length { expectedBytes = length; }
- (void)showDownloadDidReceiveDataOfLength:(uint64_t)length { downloadedBytes += length; }
- (void)showDownloadDidStartExtractingUpdate {
    extractionProgress = 0;
    setState(@"extracting", @"正在校验并准备应用更新…");
}
- (void)showExtractionReceivedProgress:(double)progress { extractionProgress = progress; }
- (void)showReadyToInstallAndRelaunch:(void (^)(SPUUserUpdateChoice))reply {
    self.pendingChoice = reply;
    setState(@"ready_to_install", installationBusy ? @"更新已准备好，等待当前任务结束后安装。" : @"更新已准备好，正在安装。");
    [self resumeInstallation];
}
- (void)showInstallingUpdateWithApplicationTerminated:(BOOL)terminated retryTerminatingApplication:(void (^)(void))retry {
    setState(@"installing", @"正在安装应用更新…");
}
- (void)showUpdateInstalledAndRelaunched:(BOOL)relaunched acknowledgement:(void (^)(void))acknowledgement {
    setState(@"completed", @"应用更新已安装。");
    acknowledgement();
}
- (void)dismissUpdateInstallation {
    self.pendingChoice = nil;
    self.pendingRelaunch = nil;
}
- (void)resumeInstallation {
    if (installationBusy) return;
    if (self.pendingChoice != nil) {
        void (^reply)(SPUUserUpdateChoice) = self.pendingChoice;
        self.pendingChoice = nil;
        reply(SPUUserUpdateChoiceInstall);
    }
    if (self.pendingRelaunch != nil) {
        void (^reply)(void) = self.pendingRelaunch;
        self.pendingRelaunch = nil;
        reply();
    }
}
@end

@interface LeapUpdaterDelegate : NSObject <SPUUpdaterDelegate>
@end

@implementation LeapUpdaterDelegate
- (BOOL)updater:(SPUUpdater *)sender mayPerformUpdateCheck:(SPUUpdateCheck)check error:(NSError **)error {
    if (checkRequested) return YES;
    if (error != NULL) *error = [NSError errorWithDomain:@"org.projectleap.update" code:2
        userInfo:@{NSLocalizedDescriptionKey: @"请通过应用中的检查更新开始更新。"}];
    return NO;
}
- (NSString *)feedURLStringForUpdater:(SPUUpdater *)sender { return configuredFeed; }
- (SUAppcastItem *)bestValidUpdateInAppcast:(SUAppcast *)appcast forUpdater:(SPUUpdater *)sender {
    if (expectedBuild.length == 0) return nil;
    for (SUAppcastItem *item in appcast.items) {
        if ([item.versionString isEqualToString:expectedBuild]) return item;
    }
    return [SUAppcastItem emptyAppcastItem];
}
- (BOOL)updater:(SPUUpdater *)sender shouldProceedWithUpdate:(SUAppcastItem *)item updateCheck:(SPUUpdateCheck)check error:(NSError **)error {
    if (expectedBuild.length == 0 || ([item.versionString isEqualToString:expectedBuild] && !item.informationOnlyUpdate)) return YES;
    if (error != NULL) *error = planError(@"应用更新与本次已确认的更新计划不一致。请重新检查更新。");
    return NO;
}
- (void)updater:(SPUUpdater *)sender didFindValidUpdate:(SUAppcastItem *)item {
    availableVersion = item.displayVersionString;
    availableBuild = item.versionString;
    if (item.informationOnlyUpdate) {
        setState(@"information_only", @"当前应用更新需要查看发行说明后手动处理。");
    } else {
        setState(@"available", [NSString stringWithFormat:@"发现应用版本 %@。", availableVersion]);
    }
}
- (void)updaterDidNotFindUpdate:(SPUUpdater *)sender error:(NSError *)error {
    if (expectedBuild.length != 0) fail(planError(@"已选定的应用更新已不再可用。请重新检查更新。"));
    else setState(@"no_update", @"当前应用已是最新可用版本。");
}
- (void)updater:(SPUUpdater *)sender willInstallUpdate:(SUAppcastItem *)item {
    setState(@"installing", @"正在安装应用更新…");
}
- (BOOL)updater:(SPUUpdater *)sender shouldPostponeRelaunchForUpdate:(SUAppcastItem *)item untilInvokingBlock:(void (^)(void))handler {
    if (!installationBusy) return NO;
    driver.pendingRelaunch = handler;
    setState(@"ready_to_install", @"更新已准备好，等待当前任务结束后安装。");
    return YES;
}
- (void)updaterWillRelaunchApplication:(SPUUpdater *)sender {
    setState(@"relaunching", @"正在重新启动更新后的应用。");
}
- (void)updater:(SPUUpdater *)sender didAbortWithError:(NSError *)error {
    if (!checkRequested) return;
    if (error.code == SUNoUpdateError && [error.domain isEqualToString:SUSparkleErrorDomain]) {
        if (expectedBuild.length != 0) fail(planError(@"已选定的应用更新已不再可用。请重新检查更新。"));
        else setState(@"no_update", @"当前应用已是最新可用版本。");
    } else fail(error);
}
- (void)updater:(SPUUpdater *)sender didFinishUpdateCycleForUpdateCheck:(SPUUpdateCheck)check error:(NSError *)error {
    if (!checkRequested) return;
    if (error != nil && error.code != SUNoUpdateError) fail(error);
    else if ([status isEqualToString:@"checking"]) setState(@"no_update", @"当前应用已是最新可用版本。");
    checkRequested = NO;
}
@end

static LeapUpdaterDelegate *delegate;

int leap_sparkle_start(void) {
    @autoreleasepool {
        if (![NSThread isMainThread]) return -2;
        if (updater != nil) return 1;
        NSBundle *bundle = NSBundle.mainBundle;
        configuredFeed = [bundle objectForInfoDictionaryKey:@"SUFeedURL"];
        NSString *key = [bundle objectForInfoDictionaryKey:@"SUPublicEDKey"];
        hostBuild = [bundle objectForInfoDictionaryKey:@"CFBundleVersion"];
        if (configuredFeed.length == 0 || key.length == 0) {
            setState(@"unconfigured", @"此版本尚未配置正式更新源与签名公钥。");
            return 0;
        }
        delegate = [[LeapUpdaterDelegate alloc] init];
        driver = [[LeapUpdateDriver alloc] init];
        updater = [[SPUUpdater alloc] initWithHostBundle:bundle applicationBundle:bundle userDriver:driver delegate:delegate];
        NSError *error = nil;
        if (![updater startUpdater:&error]) {
            fail(error);
            updater = nil;
            return -1;
        }
        setState(@"idle", @"可检查应用更新。");
        return 1;
    }
}

int leap_sparkle_check(void) {
    @autoreleasepool {
        if (![NSThread isMainThread]) return -2;
        if (updater == nil && leap_sparkle_start() != 1) return 0;
        if (updater.sessionInProgress || !updater.canCheckForUpdates || driver.pendingChoice != nil || driver.pendingRelaunch != nil) return 0;
        expectedBuild = availableBuild = availableVersion = nil;
        downloadedBytes = expectedBytes = 0; extractionProgress = 0;
        lastErrorCode = 0;
        setState(@"checking", @"正在检查应用更新…");
        checkRequested = YES;
        [updater checkForUpdateInformation];
        return 1;
    }
}

int leap_sparkle_install(const char *build, const char *feed, const char *host) {
    @autoreleasepool {
        if (![NSThread isMainThread]) return -2;
        if (updater == nil || build == NULL || feed == NULL || host == NULL) return 0;
        NSString *target = [NSString stringWithUTF8String:build];
        NSString *source = [NSString stringWithUTF8String:feed];
        NSString *original = [NSString stringWithUTF8String:host];
        if (![status isEqualToString:@"available"] || ![target isEqualToString:availableBuild] ||
            ![source isEqualToString:configuredFeed] || ![original isEqualToString:hostBuild]) {
            fail(planError(@"应用或更新源已改变。请重新检查更新。"));
            return 0;
        }
        if (updater.sessionInProgress || !updater.canCheckForUpdates) return 0;
        expectedBuild = target;
        lastErrorCode = 0;
        setState(@"checking", @"正在下载本次选定的应用更新…");
        checkRequested = YES;
        [updater checkForUpdates];
        return 1;
    }
}

void leap_sparkle_set_installation_busy(int busy) {
    @autoreleasepool {
        if (![NSThread isMainThread]) return;
        installationBusy = busy != 0;
        [driver resumeInstallation];
    }
}

const char *leap_sparkle_state_json(void) {
    @autoreleasepool {
        if (![NSThread isMainThread]) return "{\"status\":\"failed\",\"message\":\"Sparkle requires the main thread\"}";
        NSDictionary *state = @{ @"status": status, @"message": message,
            @"available_version": availableVersion ?: @"", @"available_build": availableBuild ?: @"",
            @"host_build": hostBuild ?: @"", @"feed_url": configuredFeed ?: @"", @"error_code": @(lastErrorCode),
            @"downloaded_bytes": @(downloadedBytes), @"expected_bytes": @(expectedBytes), @"extraction_progress": @(extractionProgress),
            @"installation_busy": @(installationBusy),
            @"can_check": @(updater != nil && updater.canCheckForUpdates && !updater.sessionInProgress && driver.pendingChoice == nil && driver.pendingRelaunch == nil),
            @"session_in_progress": @(updater != nil && (updater.sessionInProgress || driver.pendingChoice != nil || driver.pendingRelaunch != nil)) };
        NSData *data = [NSJSONSerialization dataWithJSONObject:state options:0 error:nil];
        NSString *serialized = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
        free(serializedBytes);
        serializedBytes = strdup(serialized.UTF8String);
        return serializedBytes;
    }
}
