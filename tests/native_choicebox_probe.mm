// Test-only observer of this process's public NSMenu entry point.
// Keyboard events are posted only inside this test process's NSApplication.
#import <AppKit/AppKit.h>
#import <objc/runtime.h>
static void (*originalPopup)(id, SEL, NSMenu *, NSEvent *, NSView *);
static BOOL (*originalSendAction)(id, SEL, SEL, id, id);
static __weak NSMenu *observedMenu;
static NSString *plan;
static NSMutableDictionary *report;
static NSMutableArray *events;
static NSString *serialized;
static NSMenu *activeMenu;
static NSView *activeView;
static NSTimer *driver;
static NSUInteger ticks, keySteps;
static NSDictionary *snapshot(NSMenu *menu) {
    NSMutableArray *items = [NSMutableArray array];
    for (NSMenuItem *item in menu.itemArray)
        [items addObject:@{@"title": item.title ?: @"", @"checked": @(item.state == NSControlStateValueOn), @"enabled": @(item.enabled)}];
    return @{@"highlighted": menu.highlightedItem.title ?: @"", @"app_active": @(NSApp.active), @"key_window": NSApp.keyWindow.title ?: @"", @"class": NSStringFromClass(menu.class), @"appearance": menu.effectiveAppearance.name ?: @"", @"items": items};
}
static void postKey(NSString *text, unsigned short code) {
    NSEvent *event = [NSEvent keyEventWithType:NSEventTypeKeyDown location:NSZeroPoint modifierFlags:0 timestamp:NSProcessInfo.processInfo.systemUptime
                               windowNumber:activeView.window.windowNumber context:nil characters:text charactersIgnoringModifiers:text isARepeat:NO keyCode:code];
    [NSApp postEvent:event atStart:NO];
    [events addObject:@{@"input": @"NSApp.keyDown", @"characters": text, @"keyCode": @(code), @"highlighted_before": activeMenu.highlightedItem.title ?: @""}];
}
static void drive(NSTimer *timer) {
    (void)timer; ticks++;
    if (ticks == 1) {
        report[@"tracking"] = snapshot(activeMenu);
        if ([plan hasPrefix:@"keyboard:"]) {
            NSInteger initial = [activeMenu indexOfItem:activeMenu.highlightedItem];
            NSInteger target = [[plan substringFromIndex:9] integerValue];
            keySteps = initial < 0 ? (NSUInteger)target + 1 : (NSUInteger)((target - initial + activeMenu.numberOfItems) % activeMenu.numberOfItems);
        }
    }
    if ([plan isEqualToString:@"escape"] && ticks == 2) postKey(@"\x1b", 53);
    if ([plan hasPrefix:@"keyboard:"]) {
        if (ticks >= 2 && ticks <= keySteps + 1) postKey([NSString stringWithFormat:@"%C", static_cast<unichar>(NSDownArrowFunctionKey)], 125);
        if (ticks == keySteps + 3) postKey(@"\r", 36);
    }
    if (ticks >= 30) { report[@"watchdog_cancelled"] = @YES; [activeMenu cancelTracking]; }
}
static void popupHook(id receiver, SEL selector, NSMenu *menu, NSEvent *event, NSView *view) {
    if (!plan) { originalPopup(receiver, selector, menu, event, view); return; }
    activeMenu = menu; observedMenu = menu; activeView = view; ticks = 0;
    report[@"native_popup_intercepted"] = @YES;
    report[@"before"] = snapshot(menu);
    driver = [NSTimer timerWithTimeInterval:0.08 repeats:YES block:^(NSTimer *timer) { drive(timer); }];
    [[NSRunLoop mainRunLoop] addTimer:driver forMode:NSEventTrackingRunLoopMode];
    originalPopup(receiver, selector, menu, event, view);
    [driver invalidate]; driver = nil;
    report[@"after"] = snapshot(menu); report[@"events"] = events;
    NSData *data = [NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted error:nil];
    serialized = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    activeMenu = nil; activeView = nil; plan = nil;
}
static BOOL sendActionHook(id receiver, SEL selector, SEL action, id target, id sender) {
    BOOL relevant = [sender isKindOfClass:NSMenuItem.class] && [observedMenu.itemArray containsObject:sender];
    NSMutableDictionary *entry = nil;
    if (relevant) {
        entry = [NSMutableDictionary dictionaryWithDictionary:@{@"input": @"NSApplication.sendAction", @"action": NSStringFromSelector(action),
                  @"target_class": NSStringFromClass([target class]) ?: @"", @"item": [(NSMenuItem *)sender title]}];
        [events addObject:entry];
    }
    BOOL result = originalSendAction(receiver, selector, action, target, sender);
    if (entry) entry[@"delivered"] = @(result);
    return result;
}
extern "C" void probe_install(void) {
    Method method = class_getClassMethod(NSMenu.class, @selector(popUpContextMenu:withEvent:forView:));
    originalPopup = (decltype(originalPopup))method_getImplementation(method);
    method_setImplementation(method, (IMP)popupHook);
    Method action = class_getInstanceMethod(NSApplication.class, @selector(sendAction:to:from:));
    originalSendAction = (decltype(originalSendAction))method_getImplementation(action);
    method_setImplementation(action, (IMP)sendActionHook);
    NSApp.appearance = [NSAppearance appearanceNamed:NSAppearanceNameAqua];
}
extern "C" void probe_prepare(const char *mode) {
    observedMenu = nil;
    plan = [NSString stringWithUTF8String:mode];
    report = [NSMutableDictionary dictionaryWithDictionary:@{@"plan": plan, @"native_popup_intercepted": @NO, @"watchdog_cancelled": @NO}];
    events = [NSMutableArray array]; serialized = @"{}";
}
extern "C" const char *probe_result(void) {
    if (observedMenu) {
        report[@"events"] = events;
        NSData *data = [NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted error:nil];
        serialized = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    }
    return serialized.UTF8String;
}
extern "C" void probe_uninstall(void) {
    if (originalPopup) {
        Method method = class_getClassMethod(NSMenu.class, @selector(popUpContextMenu:withEvent:forView:));
        method_setImplementation(method, (IMP)originalPopup);
        Method action = class_getInstanceMethod(NSApplication.class, @selector(sendAction:to:from:));
        method_setImplementation(action, (IMP)originalSendAction);
    }
    observedMenu = nil;
}
