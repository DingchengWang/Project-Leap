import Foundation

@main struct SelectionInteractionTests {
    static func main() {
        var model = SelectionInteraction()
        model.configure(centers: [20, 70, 120], enabled: [true, true, true], lowerBound: 0, upperBound: 140)
        model.confirm(index: 0)
        precondition(model.begin(at: 23))
        model.move(to: 81)
        precondition(model.center == 78 && model.selectedIndex == 0 && model.previewIndex == 1)
        model.configure(centers: [20, 70, 120], enabled: [true, true, true], lowerBound: 0, upperBound: 140)
        precondition(model.tracking, "Identical controller refresh must preserve pointer tracking")
        precondition(model.end(at: 123, inside: true) == 2)
        precondition(model.selectedIndex == 0 && model.center == 20, "Controller rejection must restore original state")
        precondition(model.end(at: 123, inside: true) == nil, "Exactly one request per mouse sequence")
        model.confirm(index: 2)
        precondition(model.selectedIndex == 2 && model.center == 120)
        for outside in [-1.0, 141.0, .nan, .infinity] {
            precondition(model.begin(at: 120)); precondition(model.end(at: outside, inside: true) == nil)
            precondition(model.selectedIndex == 2 && !model.tracking)
        }
        precondition(model.begin(at: 120)); precondition(model.end(at: 20, inside: false) == nil)
        precondition(model.begin(at: 20)); model.move(to: 60); model.cancel()
        precondition(model.selectedIndex == 2 && model.center == 120)
        precondition(model.begin(at: 20))
        model.configure(centers: [30, 80, 130], enabled: [true, true, false], lowerBound: 0, upperBound: 150)
        precondition(!model.tracking)
        precondition(!model.begin(at: 130), "Disabled options cannot start tracking")
        precondition(model.begin(at: 30)); precondition(model.end(at: 130, inside: true) == nil)
        model.confirm(index: 0); precondition(model.step(direction: 1) == 1)
        model.confirm(index: 1); precondition(model.step(direction: 1) == nil)
        model.configure(centers: [], enabled: [], lowerBound: 0, upperBound: 1)
        precondition(model.selectedIndex == nil && !model.begin(at: 0))
        print("SelectionInteraction: controller confirmation, continuous tracking, single release, bounds, cancel, resize, disabled options, empty dynamic groups passed")
    }
}
