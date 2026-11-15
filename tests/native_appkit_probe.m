// Test-only inspection through public AppKit and accessibility methods.
// This library is compiled separately and never linked into the application.
#import <AppKit/AppKit.h>
#import <objc/message.h>

static NSString *serialized;
static NSData *imageData;

void probe_enable_accessibility(void) {
    // Request the same enhanced accessibility tree used by an assistive client.
    SEL setter = NSSelectorFromString(@"accessibilitySetValue:forAttribute:");
    if ([NSApp respondsToSelector:setter])
        ((void (*)(id, SEL, id, id))objc_msgSend)(NSApp, setter, @YES, @"AXEnhancedUserInterface");
}

static id objectAttribute(id object, SEL selector) {
    return [object respondsToSelector:selector] ? ((id (*)(id, SEL))objc_msgSend)(object, selector) : nil;
}

static BOOL boolAttribute(id object, SEL selector, BOOL fallback) {
    return [object respondsToSelector:selector] ? ((BOOL (*)(id, SEL))objc_msgSend)(object, selector) : fallback;
}

static NSArray *rectArray(NSRect rect) {
    return @[@(rect.origin.x), @(rect.origin.y), @(rect.size.width), @(rect.size.height)];
}

static NSRect accessibilityFrame(id object) {
    return [object respondsToSelector:@selector(accessibilityFrame)] ?
        ((NSRect (*)(id, SEL))objc_msgSend)(object, @selector(accessibilityFrame)) : NSZeroRect;
}

static void collectAccessibility(id object, NSView *view, NSHashTable *seen, NSMutableArray *rows) {
    if (!object || [seen containsObject:object] || rows.count > 1000) return;
    [seen addObject:object];
    NSRect screen = accessibilityFrame(object);
    NSRect local = view.window ? [view convertRect:[view.window convertRectFromScreen:screen] fromView:nil] : NSZeroRect;
    id value = objectAttribute(object, @selector(accessibilityValue));
    NSMutableDictionary *row = [@{
        @"role": objectAttribute(object, @selector(accessibilityRole)) ?: @"",
        @"label": objectAttribute(object, @selector(accessibilityLabel)) ?: @"",
        @"help": objectAttribute(object, @selector(accessibilityHelp)) ?: @"",
        @"title": objectAttribute(object, @selector(accessibilityTitle)) ?: @"",
        @"identifier": objectAttribute(object, @selector(accessibilityIdentifier)) ?: @"",
        @"enabled": @(boolAttribute(object, @selector(isAccessibilityEnabled), YES)),
        @"frame": rectArray(local), @"screenFrame": rectArray(screen)
    } mutableCopy];
    if ([value isKindOfClass:NSNumber.class] || [value isKindOfClass:NSString.class]) row[@"value"] = value;
    if ([value isKindOfClass:NSString.class] && [value length] &&
        [object respondsToSelector:@selector(accessibilityAttributedStringForRange:)]) {
        NSAttributedString *text = ((id (*)(id, SEL, NSRange))objc_msgSend)(
            object, @selector(accessibilityAttributedStringForRange:), NSMakeRange(0, [value length]));
        if (text.length) {
            NSDictionary *font = [text attribute:NSAccessibilityFontTextAttribute atIndex:0 effectiveRange:NULL];
            if (font[NSAccessibilityFontSizeKey]) row[@"fontSize"] = font[NSAccessibilityFontSizeKey];
        }
    }
    if ([object isKindOfClass:NSView.class]) row[@"hidden"] = @([(NSView *)object isHiddenOrHasHiddenAncestor]);
    [rows addObject:row];
    for (id child in objectAttribute(object, @selector(accessibilityChildren))) collectAccessibility(child, view, seen, rows);
}

static NSArray *accessibilityRows(id root, NSView *view) {
    NSMutableArray *rows = [NSMutableArray array];
    NSHashTable *seen = [[NSHashTable alloc] initWithOptions:NSPointerFunctionsObjectPointerPersonality capacity:0];
    collectAccessibility(root, view, seen, rows);
    return rows;
}

static void collectViews(NSView *view, NSView *root, NSMutableArray *rows, NSMutableArray *scrolls) {
    BOOL dark = [[view.effectiveAppearance bestMatchFromAppearancesWithNames:@[NSAppearanceNameAqua, NSAppearanceNameDarkAqua]] isEqualToString:NSAppearanceNameDarkAqua];
    [rows addObject:@{@"class": NSStringFromClass(view.class), @"dark": @(dark),
                     @"hidden": @(view.isHiddenOrHasHiddenAncestor), @"frame": rectArray([view convertRect:view.bounds toView:root])}];
    if ([view isKindOfClass:NSScrollView.class]) {
        NSScrollView *scroll = (NSScrollView *)view;
        [scrolls addObject:@{@"frame": rectArray([scroll convertRect:scroll.bounds toView:root]),
                            @"document": rectArray(scroll.documentView.frame), @"offset": @(scroll.contentView.bounds.origin.y)}];
    }
    for (NSView *child in view.subviews) collectViews(child, root, rows, scrolls);
}

const char *probe_view_snapshot(void *pointer) {
    NSView *view = (__bridge NSView *)pointer;
    NSMutableArray *views = [NSMutableArray array], *scrolls = [NSMutableArray array];
    collectViews(view, view, views, scrolls);
    NSDictionary *state = @{@"attached": @(view.window != nil), @"hidden": @(view.isHiddenOrHasHiddenAncestor),
                            @"frame": rectArray(view.frame), @"elements": accessibilityRows(view, view),
                            @"windowElements": accessibilityRows(view.window, view), @"views": views, @"scrolls": scrolls};
    NSData *data = [NSJSONSerialization dataWithJSONObject:state options:0 error:nil];
    serialized = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    return serialized.UTF8String;
}

static id findAccessibility(id object, NSString *identifier, NSHashTable *seen) {
    if (!object || [seen containsObject:object]) return nil;
    [seen addObject:object];
    if ([objectAttribute(object, @selector(accessibilityIdentifier)) isEqualToString:identifier]) return object;
    for (id child in objectAttribute(object, @selector(accessibilityChildren))) {
        id result = findAccessibility(child, identifier, seen);
        if (result) return result;
    }
    return nil;
}

int probe_accessibility_press(void *pointer, const char *identifier) {
    NSView *view = (__bridge NSView *)pointer;
    if (view.isHiddenOrHasHiddenAncestor) return 0;
    NSHashTable *seen = [[NSHashTable alloc] initWithOptions:NSPointerFunctionsObjectPointerPersonality capacity:0];
    id target = findAccessibility(view, [NSString stringWithUTF8String:identifier], seen);
    return target && boolAttribute(target, @selector(isAccessibilityEnabled), YES) &&
        boolAttribute(target, @selector(accessibilityPerformPress), NO);
}

const void *probe_application_icon_png(size_t *length) {
    NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc] initWithData:NSApp.applicationIconImage.TIFFRepresentation];
    imageData = [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
    *length = imageData.length;
    return imageData.bytes;
}

const void *probe_view_png(void *pointer, size_t *length) {
    NSView *view = (__bridge NSView *)pointer;
    NSBitmapImageRep *bitmap = [view bitmapImageRepForCachingDisplayInRect:view.bounds];
    [view cacheDisplayInRect:view.bounds toBitmapImageRep:bitmap];
    imageData = [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
    *length = imageData.length;
    return imageData.bytes;
}
