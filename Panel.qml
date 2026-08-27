import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar headline reader for any RSS feed (Yahoo Finance by default), styled as
// a wire-service ticker rather than a search list: the bar entry shows the
// live latest headline instead of a static label, and the panel reads top
// to bottom like a press wire — one featured story, then a numbered feed
// of the rest, set entirely in the shell's monospace theme font.
//
// Left click (or keyboard summon) opens the panel; typing filters the
// already-fetched headlines by title or source. Enter or a click opens a
// headline in the default browser. Fetching is done by fetch-news.py, which
// only reads the configured feed over HTTP(S) with the Python standard
// library, so no extra runtime dependency beyond Python 3 is required.
Panel {
  id: root

  moduleName: "synapsync.news-feed"
  ipcTarget: "synapsync.news-feed"

  readonly property color foreground: bar ? bar.barForeground : Color.foreground
  readonly property color accent: Color.accent
  readonly property color dim: Qt.darker(foreground, 1.5)
  readonly property color dimmer: Qt.darker(foreground, 2.1)
  readonly property color hairline: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.16)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string fetchCommand: (Quickshell.env("HOME") || "")
    + "/.config/omarchy/plugins/synapsync.news-feed/fetch-news.py"

  readonly property string feedUrl: String(root.setting("feedUrl", "https://finance.yahoo.com/news/rssindex")).trim()
  readonly property string feedName: {
    var name = String(root.setting("feedName", "Yahoo Finance")).trim()
    return name !== "" ? name : "News Feed"
  }
  readonly property int itemLimit: {
    var n = parseInt(root.setting("itemLimit", "25"), 10)
    return (isNaN(n) || n < 1) ? 25 : Math.min(n, 50)
  }
  readonly property int refreshMinutes: {
    var n = parseInt(root.setting("refreshMinutes", "15"), 10)
    return isNaN(n) || n < 0 ? 15 : n
  }

  property var items: []
  property int selectedIndex: -1
  property bool loading: false
  property bool editingSettings: false
  property string lastError: ""
  property double lastFetchedAt: 0

  readonly property int maxListRows: 6
  readonly property real rowHeight: Style.space(44)
  readonly property string query: filterField.text.trim()
  readonly property bool browsing: root.query === ""

  readonly property var filteredItems: {
    if (root.query === "") return root.items
    var q = root.query.toLowerCase()
    return root.items.filter(function(it) {
      return (it.title || "").toLowerCase().indexOf(q) >= 0
        || (it.source || "").toLowerCase().indexOf(q) >= 0
    })
  }

  // Browsing the unfiltered feed gives the newest item a featured "top
  // story" treatment; a search result set is a flat, numbered list instead —
  // a hero card among search matches would outrank relevance with recency.
  readonly property var leadItem: root.browsing && root.filteredItems.length > 0 ? root.filteredItems[0] : null
  readonly property var listItems: root.leadItem ? root.filteredItems.slice(1) : root.filteredItems
  readonly property int listOffset: root.leadItem ? 1 : 0

  readonly property bool hasItems: filteredItems.length > 0

  readonly property string tickerText: {
    if (root.items.length === 0) return "News"
    var headline = String(root.items[0].title || "").trim()
    if (headline === "") return "News"
    return headline.length > 34 ? headline.slice(0, 33) + "…" : headline
  }

  readonly property string footerCountText: {
    var n = filteredItems.length
    return n + (n === 1 ? " headline" : " headlines")
  }

  visible: true
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ---- formatting helpers ---------------------------------------------

  function agoText(epoch) {
    if (!epoch) return ""
    var seconds = Math.max(0, (Date.now() / 1000) - Number(epoch))
    if (seconds < 60) return "now"
    var minutes = Math.floor(seconds / 60)
    if (minutes < 60) return minutes + "m"
    var hours = Math.floor(minutes / 60)
    if (hours < 24) return hours + "h"
    var days = Math.floor(hours / 24)
    return days + "d"
  }

  function rowNumber(listIndex) {
    var n = listIndex + root.listOffset + 1
    return n < 10 ? "0" + n : String(n)
  }

  // ---- actions -----------------------------------------------------------

  function runFetch() {
    if (fetchProcess.running) return
    if (root.feedUrl === "") {
      root.loading = false
      root.items = []
      root.selectedIndex = -1
      root.lastError = "Set a feed URL to get started"
      return
    }
    root.loading = true
    root.lastError = ""
    fetchProcess.command = ["python3", root.fetchCommand, root.feedUrl, String(root.itemLimit)]
    fetchProcess.running = true
  }

  function persistSettings(feedUrl, feedName, itemLimit, refreshMinutes) {
    var entry = { id: root.moduleName }
    for (var key in root.settings) if (key !== "id") entry[key] = root.settings[key]
    entry.feedUrl = String(feedUrl || "")
    entry.feedName = String(feedName || "")
    entry.itemLimit = String(itemLimit || "25")
    entry.refreshMinutes = String(refreshMinutes || "15")

    root.settings = entry
    if (root.bar && root.bar.shell && typeof root.bar.shell.updateEntryInline === "function")
      root.bar.shell.updateEntryInline(root.moduleName, entry)

    root.lastError = ""
    Qt.callLater(root.runFetch)
  }

  function parseItems(raw) {
    var text = String(raw || "").trim()
    root.loading = false
    root.lastFetchedAt = Date.now() / 1000
    if (text === "") {
      root.items = []
      root.selectedIndex = -1
      return
    }
    try {
      var parsed = JSON.parse(text)
      root.items = (parsed && Array.isArray(parsed)) ? parsed : []
    } catch (e) {
      console.warn(root.moduleName + ": invalid fetch output", e)
      root.lastError = "Invalid feed response"
      root.items = []
    }
    root.selectedIndex = root.filteredItems.length > 0 ? 0 : -1
    root.ensureVisible()
  }

  function ensureVisible() {
    if (root.leadItem && root.selectedIndex <= 0) {
      resultList.contentY = 0
      return
    }
    var listIndex = root.selectedIndex - root.listOffset
    if (listIndex >= 0) resultList.positionViewAtIndex(listIndex, ListView.Contain)
  }

  function move(delta) {
    var n = root.filteredItems.length
    if (n <= 0) return
    root.selectedIndex = Math.max(0, Math.min(n - 1, root.selectedIndex + delta))
    root.ensureVisible()
  }

  function openHeadline() {
    if (root.selectedIndex < 0 || root.selectedIndex >= root.filteredItems.length) return
    var headline = root.filteredItems[root.selectedIndex]
    if (!headline || !headline.link) return
    root.close()
    Qt.callLater(function() {
      Quickshell.execDetached(["xdg-open", headline.link])
    })
  }

  function startEditingSettings() {
    root.editingSettings = true
    feedUrlField.text = root.feedUrl
    feedNameField.text = root.feedName
    limitField.text = String(root.itemLimit)
    intervalField.text = String(root.refreshMinutes)
    Qt.callLater(function() { feedUrlField.forceActiveFocus() })
  }

  function saveSettings() {
    root.editingSettings = false
    root.persistSettings(feedUrlField.text.trim(), feedNameField.text.trim(),
      limitField.text.trim(), intervalField.text.trim())
    Qt.callLater(function() { filterField.forceActiveFocus() })
  }

  onOpenedChanged: if (opened) {
    filterField.text = ""
    root.editingSettings = false
    if (Date.now() / 1000 - root.lastFetchedAt > 30) root.runFetch()
    Qt.callLater(function() {
      if (!root.editingSettings) filterField.forceActiveFocus()
    })
  }

  Component.onCompleted: root.runFetch()

  Process {
    id: fetchProcess
    command: []
    running: false

    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.parseItems(text)
    }

    onExited: function(exitCode) {
      root.loading = false
      if (exitCode !== 0) {
        console.warn(root.moduleName + ": fetch command exited", exitCode)
        root.lastError = "Fetch failed (error " + exitCode + ")"
      }
    }
  }

  Timer {
    running: root.refreshMinutes > 0
    interval: Math.max(1, root.refreshMinutes) * 60 * 1000
    repeat: true
    onTriggered: root.runFetch()
  }

  // ---- bar entry: a live ticker, not a static label -------------------------

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: (root.loading && root.items.length === 0 ? "News…" : "» " + root.tickerText)
    fontSize: Style.font.bodySmall
    horizontalMargin: 6.5
    tooltipText: root.feedName + " — click to open, right-click to refresh"

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) {
        root.runFetch()
      } else {
        root.toggle()
      }
    }
  }

  // ---- headlines panel -------------------------------------------------------

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: root.editingSettings ? feedUrlField : filterField
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(contentColumn.implicitHeight, Style.space(620))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: filterField.activeFocus || feedUrlField.activeFocus
        || feedNameField.activeFocus || limitField.activeFocus || intervalField.activeFocus

      onMoveRequested: function(dx, dy) { if (dy !== 0) root.move(dy) }
      onActivateRequested: root.openHeadline()
      onReturnRequested: root.openHeadline()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        filterField.insert(filterField.cursorPosition, t)
        filterField.forceActiveFocus()
      }

      Column {
        id: contentColumn
        width: parent.width
        spacing: 0

        // ---- masthead ------------------------------------------------------

        Item {
          visible: !root.editingSettings
          width: parent.width
          height: Style.space(24)

          Row {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(7)

            Text {
              text: root.feedName
              font.capitalization: Font.AllUppercase
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              font.bold: true
              font.letterSpacing: 1.1
              color: root.foreground
            }

            Text {
              text: "wire"
              font.capitalization: Font.AllUppercase
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1.4
              color: root.accent
            }
          }

          Row {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(4)

            Button {
              iconText: "󰑐"
              tooltipText: "Refresh"
              foreground: root.dim
              fontFamily: root.fontFamily
              iconSize: Style.font.icon
              horizontalPadding: Style.space(6)
              iconSpinning: root.loading
              focusable: true
              onClicked: root.runFetch()
            }

            Button {
              iconText: "󰒓"
              tooltipText: "Feed settings"
              foreground: root.dim
              fontFamily: root.fontFamily
              iconSize: Style.font.icon
              horizontalPadding: Style.space(6)
              focusable: true
              onClicked: root.startEditingSettings()
            }
          }
        }

        Rectangle {
          visible: !root.editingSettings
          width: parent.width
          height: 1
          color: root.hairline
        }

        // ---- feed settings ---------------------------------------------------

        Column {
          visible: root.editingSettings
          width: parent.width
          spacing: Style.spacing.sm
          topPadding: Style.space(4)
          bottomPadding: Style.space(4)

          Text {
            text: "feed settings"
            font.capitalization: Font.AllUppercase
            color: root.accent
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.letterSpacing: 1.4
            font.bold: true
          }

          TextField {
            id: feedUrlField
            width: parent.width
            placeholderText: "https://…/rss"
            foreground: root.foreground
            Keys.onEscapePressed: root.editingSettings = false
          }

          TextField {
            id: feedNameField
            width: parent.width
            placeholderText: "Feed name shown above the list"
            foreground: root.foreground
            Keys.onEscapePressed: root.editingSettings = false
          }

          Row {
            width: parent.width
            spacing: Style.spacing.sm

            TextField {
              id: limitField
              width: (parent.width - parent.spacing) / 2
              placeholderText: "Headlines (1-50)"
              foreground: root.foreground
              validator: IntValidator { bottom: 1; top: 50 }
              Keys.onEscapePressed: root.editingSettings = false
            }

            TextField {
              id: intervalField
              width: (parent.width - parent.spacing) / 2
              placeholderText: "Refresh minutes"
              foreground: root.foreground
              validator: IntValidator { bottom: 0; top: 1440 }
              Keys.onEscapePressed: root.editingSettings = false
            }
          }

          Row {
            spacing: Style.spacing.sm
            topPadding: Style.space(2)

            Button {
              text: "Save"
              foreground: root.foreground
              fontFamily: root.fontFamily
              fontSize: Style.font.bodySmall
              bordered: true
              focusable: true
              onClicked: root.saveSettings()
            }

            Button {
              text: "Cancel"
              foreground: root.foreground
              fontFamily: root.fontFamily
              fontSize: Style.font.bodySmall
              focusable: true
              onClicked: root.editingSettings = false
            }
          }
        }

        // ---- search ---------------------------------------------------------

        Item {
          visible: !root.editingSettings
          width: parent.width
          height: visible ? Style.space(46) : 0

          TextField {
            id: filterField
            anchors.centerIn: parent
            width: parent.width
            placeholderText: "Search the wire…"
            foreground: root.foreground

            onAccepted: root.openHeadline()
            Keys.onUpPressed: root.move(-1)
            Keys.onDownPressed: root.move(1)
            Keys.onEscapePressed: {
              if (filterField.text !== "") filterField.text = ""
              else root.close()
            }
          }
        }

        // ---- lead story -------------------------------------------------------

        Column {
          visible: !root.editingSettings && root.leadItem !== null
          width: parent.width
          spacing: Style.space(6)
          bottomPadding: Style.space(14)

          Row {
            spacing: Style.space(6)

            Rectangle {
              width: Style.space(5); height: Style.space(5); radius: width / 2
              color: root.accent
              anchors.verticalCenter: parent.verticalCenter
            }

            Text {
              text: "top story"
              font.capitalization: Font.AllUppercase
              color: root.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1.4
              font.bold: true
            }
          }

          Rectangle {
            width: parent.width
            height: leadColumn.implicitHeight + Style.space(16)
            radius: Style.space(2)
            color: root.selectedIndex === 0 ? Style.selectedFillFor(root.foreground, root.accent) : "transparent"

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              onEntered: root.selectedIndex = 0
              onClicked: { root.selectedIndex = 0; root.openHeadline() }
            }

            Column {
              id: leadColumn
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.top: parent.top
              anchors.margins: Style.space(8)
              spacing: Style.space(6)

              Text {
                width: parent.width
                text: root.leadItem ? (root.leadItem.title || "") : ""
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.heading
                font.bold: true
                lineHeight: 1.15
              }

              Text {
                width: parent.width
                visible: root.leadItem && (root.leadItem.snippet || "") !== ""
                text: root.leadItem ? (root.leadItem.snippet || "") : ""
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Text {
                text: root.leadItem
                  ? [root.leadItem.source, root.agoText(root.leadItem.published) ? root.agoText(root.leadItem.published) + " ago" : ""]
                    .filter(function(s) { return s }).join("  ·  ")
                  : ""
                font.capitalization: Font.AllUppercase
                color: root.dimmer
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.letterSpacing: 0.6
              }
            }
          }
        }

        // ---- section divider ---------------------------------------------------

        Item {
          visible: !root.editingSettings && root.hasItems
          width: parent.width
          height: Style.space(20)

          Text {
            id: latestLabel
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: root.browsing ? "latest" : "matches"
            font.capitalization: Font.AllUppercase
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.letterSpacing: 1.4
            font.bold: true
          }

          Text {
            id: countLabel
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: root.footerCountText
            font.capitalization: Font.AllUppercase
            color: root.dimmer
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.letterSpacing: 0.6
          }

          Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: latestLabel.right
            anchors.leftMargin: Style.space(8)
            anchors.right: countLabel.left
            anchors.rightMargin: Style.space(8)
            height: 1
            color: root.hairline
          }
        }

        // ---- wire list ----------------------------------------------------------

        ListView {
          id: resultList
          visible: !root.editingSettings
          width: parent.width
          height: Math.min(root.listItems.length, root.maxListRows) * root.rowHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          flickableDirection: Flickable.VerticalFlick
          interactive: root.listItems.length > root.maxListRows
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

          model: root.listItems
          currentIndex: root.selectedIndex - root.listOffset

          delegate: Item {
            id: row
            required property int index
            readonly property var headline: root.listItems[index] || ({})
            readonly property int absoluteIndex: index + root.listOffset
            width: resultList.width
            height: root.rowHeight

            Rectangle {
              anchors.fill: parent
              color: root.selectedIndex === row.absoluteIndex
                ? Style.selectedFillFor(root.foreground, root.accent)
                : "transparent"
            }

            Rectangle {
              anchors.bottom: parent.bottom
              width: parent.width
              height: 1
              color: root.hairline
            }

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              onEntered: root.selectedIndex = row.absoluteIndex
              onPositionChanged: root.selectedIndex = row.absoluteIndex
              onClicked: {
                root.selectedIndex = row.absoluteIndex
                root.openHeadline()
              }
            }

            Row {
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.leftMargin: Style.space(2)
              anchors.rightMargin: Style.space(2)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(10)

              Text {
                text: root.rowNumber(row.index)
                width: Style.space(18)
                color: root.dimmer
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                width: parent.width - Style.space(18) - metaColumn.width - parent.spacing * 2
                text: row.headline.title || "…"
                elide: Text.ElideRight
                wrapMode: Text.NoWrap
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                anchors.verticalCenter: parent.verticalCenter
              }

              Column {
                id: metaColumn
                width: Style.space(76)
                spacing: Style.space(1)
                anchors.verticalCenter: parent.verticalCenter

                Text {
                  width: parent.width
                  text: row.headline.source || ""
                  font.capitalization: Font.AllUppercase
                  elide: Text.ElideRight
                  horizontalAlignment: Text.AlignRight
                  color: root.dimmer
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }

                Text {
                  width: parent.width
                  text: root.agoText(row.headline.published)
                  horizontalAlignment: Text.AlignRight
                  color: root.dimmer
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }
        }

        // ---- empty / loading / error states ----------------------------------

        Text {
          width: parent.width
          visible: !root.editingSettings && (!root.hasItems || root.lastError !== "")
          text: {
            if (root.loading && !root.hasItems) return "receiving…"
            if (root.lastError !== "") return root.lastError
            return root.query === "" ? "— wire is quiet —" : "no matches for “" + root.query + "”"
          }
          font.capitalization: root.lastError === "" ? Font.AllUppercase : Font.MixedCase
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          font.letterSpacing: 0.8
          horizontalAlignment: Text.AlignHCenter
          topPadding: Style.space(22)
          bottomPadding: Style.space(22)
        }

        // ---- footer ---------------------------------------------------------

        Item {
          visible: !root.editingSettings
          width: parent.width
          height: visible ? Style.space(28) : 0

          Rectangle {
            anchors.top: parent.top
            width: parent.width
            height: 1
            color: root.hairline
          }

          Text {
            anchors.bottom: parent.bottom
            anchors.right: parent.right
            text: "↑↓ navigate  ·  ⏎ open  ·  esc close"
            font.capitalization: Font.AllUppercase
            color: root.dimmer
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.letterSpacing: 0.6
          }
        }
      }
    }
  }
}
