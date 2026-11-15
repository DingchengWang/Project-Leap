import Foundation

/// One-dimensional pointer tracking, shared by vertical and horizontal controls.
/// Only `confirm` accepts controller state. A release returns one candidate and
/// never commits locally; rejection leaves the previous selection intact.
struct SelectionInteraction {
    private(set) var selectedIndex: Int?
    private(set) var tracking = false
    private(set) var center = 0.0
    private var centers: [Double] = []
    private var enabled: [Bool] = []
    private var lowerBound = 0.0
    private var upperBound = 0.0
    private var grabOffset = 0.0
    var previewIndex: Int? { tracking ? nearestIndex(to: center) : selectedIndex }

    mutating func configure(centers: [Double], enabled: [Bool], lowerBound: Double, upperBound: Double) {
        precondition(centers.count == enabled.count)
        precondition(lowerBound.isFinite && upperBound.isFinite && lowerBound <= upperBound)
        precondition(centers.allSatisfy { $0.isFinite && $0 >= lowerBound && $0 <= upperBound })
        precondition(zip(centers, centers.dropFirst()).allSatisfy { $0.0 < $0.1 })
        guard centers != self.centers || enabled != self.enabled || lowerBound != self.lowerBound || upperBound != self.upperBound else { return }
        self.centers = centers; self.enabled = enabled
        self.lowerBound = lowerBound; self.upperBound = upperBound
        if let selectedIndex, !centers.indices.contains(selectedIndex) { self.selectedIndex = nil }
        cancel()
    }
    @discardableResult mutating func begin(at position: Double) -> Bool {
        guard !tracking, contains(position), let index = nearestIndex(to: position), enabled[index] else { return false }
        center = centers[index]; grabOffset = position-center; tracking = true
        return true
    }
    mutating func move(to position: Double) {
        guard tracking, position.isFinite, let first = centers.first, let last = centers.last else { return }
        center = min(max(position-grabOffset, first), last)
    }
    @discardableResult mutating func end(at position: Double, inside: Bool) -> Int? {
        guard tracking else { return nil }
        guard inside, contains(position) else { cancel(); return nil }
        move(to: position)
        let candidate = nearestIndex(to: center)
        cancel()
        guard let candidate, enabled[candidate] else { return nil }
        return candidate
    }
    mutating func cancel() {
        tracking = false; grabOffset = 0
        center = selectedIndex.map { centers[$0] } ?? centers.first ?? 0
    }
    mutating func confirm(index: Int?) {
        guard index == nil || centers.indices.contains(index!) else { return }
        guard index != selectedIndex else { return }
        selectedIndex = index; cancel()
    }
    func step(direction: Int) -> Int? {
        guard !centers.isEmpty else { return nil }
        var index = (selectedIndex ?? (direction > 0 ? -1 : centers.count)) + direction
        while centers.indices.contains(index) {
            if enabled[index] { return index }
            index += direction
        }
        return nil
    }
    private func contains(_ position: Double) -> Bool {
        !centers.isEmpty && position.isFinite && position >= lowerBound && position <= upperBound
    }
    private func nearestIndex(to position: Double) -> Int? {
        guard !centers.isEmpty else { return nil }
        var nearest = 0
        for index in centers.indices.dropFirst() {
            if abs(centers[index]-position) < abs(centers[nearest]-position) { nearest = index }
        }
        return nearest
    }
}
