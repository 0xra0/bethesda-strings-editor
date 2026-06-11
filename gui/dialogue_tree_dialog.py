"""
Dialogue Tree Visualizer.

Shows the Quest → Topic → Response hierarchy from an ESP/ESM file as a
two-panel UI: a QTreeWidget on the left for navigation, and a QGraphicsScene
node graph on the right that visualises each topic's conversation flow.

Background parsing runs on a QThread so the UI stays responsive.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import (
    QObject, QPointF, QRectF, QSize, QThread, Qt, Signal, Slot,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont,
    QPainter, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGraphicsItem, QGraphicsObject,
    QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from bethesda_strings.dialogue_tree import (
    DialogueTree, QuestNode, ResponseNode, TopicNode,
    build_dialogue_tree,
)
from gui.file_dialog_helper import get_open_filename

logger = logging.getLogger(__name__)

# ── Starfield game-font helper ────────────────────────────────────────────────
# Shares the font registry from visual_context_preview (QFontDatabase is app-global).

def _game_font(size: int, bold: bool = False) -> QFont:
    """RF_35_M (Starfield body/Cyrillic) at *size* pt, or system sans-serif fallback."""
    try:
        from gui.visual_context_preview import _ensure_fonts, _registered  # noqa: PLC0415
        _ensure_fonts()
        family = _registered.get("bold" if bold else "body", "")
    except Exception:
        family = ""
    f = QFont(family) if family else QFont()
    f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setPointSize(size)
    if bold and not family:
        f.setBold(True)
    return f


# ── Background tile ───────────────────────────────────────────────────────────
# The same 50×50 noise tile used by the subtitle panel.

_DATA_DIR = Path(__file__).parent.parent / "data"
_tile_px: QPixmap | None = None


def _bg_tile() -> QPixmap | None:
    global _tile_px
    if _tile_px is not None:
        return _tile_px
    p = _DATA_DIR / "dialogue_bg_tile.png"
    if p.exists():
        _tile_px = QPixmap(str(p))
    return _tile_px


# ── Tree item roles ────────────────────────────────────────────────────────────
_ROLE_KIND    = Qt.UserRole          # 'quest' | 'topic' | 'response'
_ROLE_FORM_ID = Qt.UserRole + 1     # int


# ── Graph constants ────────────────────────────────────────────────────────────
_CARD_W     = 340
_PLAYER_H   = 58
_NPC_H      = 80
_CARD_H     = _PLAYER_H + _NPC_H + 2   # total = 140
_V_GAP      = 48    # vertical gap between chained cards (space for arrow)
_CHAIN_GAP  = 22    # extra gap between separate chains
_PAD        = 10    # text padding inside sections

# Starfield UI palette (verified from dialoguemenu.swf pixel analysis)
# Fills use pre-multiplied RGBA so they layer correctly over the dark canvas.
_C_GRAPH_BG      = QColor("#080d16")          # dark space background
_C_CARD_BG       = QColor(0, 0, 0, 153)       # card base: black alpha=153 (60 %)
_C_PLAYER_BG     = QColor(10, 18, 36, 180)    # player section: slightly bluer dark
_C_NPC_BG        = QColor(0, 0, 0, 127)       # NPC section: black alpha=127 (50 %)
_C_PLAYER_TEXT   = QColor("#c0c8d8")           # player text: soft white-blue
_C_NPC_TEXT      = QColor("#e8ecf8")           # NPC text: bright white
_C_BORDER        = QColor(255, 255, 255, 38)   # card border: white alpha=38 (15 %)
_C_BORDER_TOP    = QColor(255, 255, 255, 51)   # top edge: white alpha=51 (20 %)
_C_BORDER_BTM    = QColor(255, 255, 255, 19)   # bottom fade: white alpha=19 (7 %)
_C_BORDER_SEL    = QColor("#3ff0ff")           # selected: Starfield cyan accent
_C_SEP           = QColor(255, 255, 255, 22)   # player/NPC separator
_C_IND           = QColor(60, 100, 140, 180)   # left-bar indicator (player section)
_C_LABEL         = QColor("#2e4060")           # formID watermark
_C_ARROW         = QColor(80, 140, 200, 200)   # connection arrows: muted blue
_C_CHAIN_SEP     = QColor(30, 50, 80, 120)     # dashed chain separator


# ── Helpers ────────────────────────────────────────────────────────────────────

def _trunc(text: str, n: int) -> str:
    return text[:n] + "…" if len(text) > n else text


def _build_chains(responses: List[ResponseNode]) -> List[List[ResponseNode]]:
    """Group responses into PNAM-ordered chains; each chain is one conversation thread."""
    if not responses:
        return []

    resp_set = {r.form_id for r in responses}
    resp_map = {r.form_id: r for r in responses}

    # first successor per node (extra children become new roots)
    successor:   Dict[int, int] = {}
    extra_roots: List[int]      = []
    for r in responses:
        if r.prev_form_id in resp_set:
            if r.prev_form_id not in successor:
                successor[r.prev_form_id] = r.form_id
            else:
                extra_roots.append(r.form_id)

    roots = [r.form_id for r in responses if r.prev_form_id not in resp_set]

    placed: set  = set()
    chains: List[List[ResponseNode]] = []
    queue  = list(roots)
    i = 0
    while i < len(queue):
        root = queue[i]
        i += 1
        if root in placed:
            continue
        chain: List[ResponseNode] = []
        fid = root
        while fid and fid not in placed and fid in resp_map:
            placed.add(fid)
            chain.append(resp_map[fid])
            kids_extras = [
                r.form_id for r in responses
                if r.prev_form_id == fid and r.form_id != successor.get(fid)
                   and r.form_id not in placed
            ]
            queue.extend(kids_extras)
            fid = successor.get(fid, 0)
        if chain:
            chains.append(chain)

    # anything not yet placed (data anomalies)
    for r in responses:
        if r.form_id not in placed:
            chains.append([r])

    return chains


# ── Node graph items ───────────────────────────────────────────────────────────

class _ResponseCard(QGraphicsObject):
    """A clickable card showing one INFO record's player prompt and NPC line.

    Styled after the Starfield dialogue menu (dialoguemenu.swf):
      - Sharp corners (no rounding), matching Scaleform UI geometry
      - Fill: black at varying opacity, same as the subtitle panel
      - Border: white at 15 % / 20 % opacity (pixel-measured from SWF sprite)
      - Selected: Starfield cyan (#3ff0ff) border
      - Font: RF_35_M (extracted from fonts_uk.swf)
    """

    clicked: Signal = Signal(int)   # emits form_id

    def __init__(self, response: ResponseNode, parent=None) -> None:
        super().__init__(parent)
        self._resp     = response
        self._selected = False
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        fid  = f"0x{response.form_id:08X}"
        edid = response.edid or ""
        self.setToolTip(f"{fid}  {edid}" if edid else fid)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, _CARD_W, _CARD_H)

    def set_selected(self, sel: bool) -> None:
        if self._selected != sel:
            self._selected = sel
            self.update()

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        # ── Card fill ─────────────────────────────────────────────────────────
        painter.setPen(Qt.NoPen)
        painter.setBrush(_C_CARD_BG)
        painter.drawRect(0, 0, _CARD_W, _CARD_H)

        # Player prompt section (top)
        p_rect = QRectF(0, 0, _CARD_W, _PLAYER_H)
        painter.setBrush(_C_PLAYER_BG)
        painter.drawRect(p_rect)

        # 3-px left indicator bar on player section
        painter.setBrush(_C_IND)
        painter.drawRect(QRectF(0, 0, 3, _PLAYER_H))

        # NPC line section (bottom)
        n_y    = _PLAYER_H + 1
        n_rect = QRectF(0, n_y, _CARD_W, _CARD_H - n_y)
        painter.setBrush(_C_NPC_BG)
        painter.drawRect(n_rect)

        # Noise tile over NPC section
        tile = _bg_tile()
        if tile and not tile.isNull():
            painter.setOpacity(0.22)
            painter.drawTiledPixmap(n_rect.toRect(), tile)
            painter.setOpacity(1.0)

        # ── Borders (pixel-exact from sprite) ─────────────────────────────────
        # Top: white alpha=51 (20 %)
        painter.setPen(QPen(_C_BORDER_TOP, 1, Qt.SolidLine))
        painter.drawLine(0, 0, _CARD_W - 1, 0)
        # Left + right: white alpha=38 (15 %)
        painter.setPen(QPen(_C_BORDER, 1, Qt.SolidLine))
        painter.drawLine(0, 0, 0, _CARD_H - 1)
        painter.drawLine(_CARD_W - 1, 0, _CARD_W - 1, _CARD_H - 1)
        # Section separator
        painter.setPen(QPen(_C_SEP, 1, Qt.SolidLine))
        painter.drawLine(3, _PLAYER_H, _CARD_W - 1, _PLAYER_H)
        # Bottom: white alpha=19 (7 %)
        painter.setPen(QPen(_C_BORDER_BTM, 1, Qt.SolidLine))
        painter.drawLine(0, _CARD_H - 1, _CARD_W - 1, _CARD_H - 1)

        # Selection highlight: cyan border (Starfield accent)
        if self._selected:
            painter.setPen(QPen(_C_BORDER_SEL, 2, Qt.SolidLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(1, 1, _CARD_W - 2, _CARD_H - 2)

        # ── Text ──────────────────────────────────────────────────────────────
        font9 = _game_font(9)
        painter.setFont(font9)

        # Player text
        painter.setPen(_C_PLAYER_TEXT)
        p_text_r = p_rect.adjusted(_PAD + 3, _PAD - 2, -_PAD, -3)
        painter.drawText(p_text_r, Qt.TextWordWrap | Qt.AlignTop,
                         _trunc(self._resp.player_prompt or "—", 120))

        # NPC text
        npc_font = _game_font(9)
        painter.setFont(npc_font)
        painter.setPen(_C_NPC_TEXT)
        npc_r = n_rect.adjusted(_PAD, _PAD - 2, -_PAD, -14)
        painter.drawText(npc_r, Qt.TextWordWrap | Qt.AlignTop,
                         _trunc(self._resp.npc_line or "—", 160))

        # FormID watermark (bottom-right, very subtle)
        tiny = _game_font(7)
        painter.setFont(tiny)
        painter.setPen(_C_LABEL)
        fid_rect = QRectF(0, _CARD_H - 13, _CARD_W - 4, 12)
        painter.drawText(fid_rect, Qt.AlignRight | Qt.AlignVCenter,
                         f"0x{self._resp.form_id:08X}")

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self._resp.form_id)
        super().mousePressEvent(event)

    def hoverEnterEvent(self, event) -> None:
        if not self._selected:
            self.setOpacity(0.80)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.setOpacity(1.0)
        super().hoverLeaveEvent(event)


class _Arrow(QGraphicsItem):
    """Directional arrow between two points."""

    _PEN   = QPen(_C_ARROW, 1.5, Qt.SolidLine, Qt.RoundCap)
    _BRUSH = QBrush(_C_ARROW)

    def __init__(self, start: QPointF, end: QPointF, parent=None) -> None:
        super().__init__(parent)
        self._start = start
        self._end   = end

    def boundingRect(self) -> QRectF:
        x1, y1 = self._start.x(), self._start.y()
        x2, y2 = self._end.x(),   self._end.y()
        return QRectF(min(x1, x2) - 6, min(y1, y2) - 6,
                      abs(x2 - x1) + 12, abs(y2 - y1) + 12)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        painter.setPen(self._PEN)
        painter.drawLine(self._start, self._end)

        dx = self._end.x() - self._start.x()
        dy = self._end.y() - self._start.y()
        length = math.hypot(dx, dy)
        if length < 1:
            return
        ux, uy = dx / length, dy / length
        sz  = 8.0
        tip = self._end
        p1  = QPointF(tip.x() - sz * ux + sz * 0.42 * uy,
                      tip.y() - sz * uy - sz * 0.42 * ux)
        p2  = QPointF(tip.x() - sz * ux - sz * 0.42 * uy,
                      tip.y() - sz * uy + sz * 0.42 * ux)
        painter.setBrush(self._BRUSH)
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(QPolygonF([tip, p1, p2]))


# ── Graph view ─────────────────────────────────────────────────────────────────

class _DialogueGraphView(QGraphicsView):
    response_selected: Signal = Signal(int)   # form_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(_C_GRAPH_BG))
        self._cards: Dict[int, _ResponseCard] = {}
        self._selected_fid: Optional[int] = None

    def load_responses(self, responses: List[ResponseNode]) -> None:
        self._scene.clear()
        self._cards.clear()
        self._selected_fid = None

        if not responses:
            item = self._scene.addText("No responses in this topic.")
            item.setDefaultTextColor(_C_NPC_TEXT)
            return

        chains = _build_chains(responses)
        y = 0.0

        for chain_idx, chain in enumerate(chains):
            if chain_idx > 0:
                sep_y = y - (_CHAIN_GAP / 2 + 1)
                line  = self._scene.addLine(-12, sep_y, _CARD_W + 12, sep_y)
                line.setPen(QPen(_C_CHAIN_SEP, 1, Qt.DashLine))

            prev_card_y: Optional[float] = None

            for resp in chain:
                card = _ResponseCard(resp)
                card.setPos(0.0, y)
                card.clicked.connect(self._on_card_clicked)
                self._scene.addItem(card)
                self._cards[resp.form_id] = card

                if prev_card_y is not None:
                    mid_x = _CARD_W / 2
                    arrow = _Arrow(
                        QPointF(mid_x, prev_card_y + _CARD_H),
                        QPointF(mid_x, y),
                    )
                    self._scene.addItem(arrow)

                prev_card_y = y
                y += _CARD_H + _V_GAP

            y += _CHAIN_GAP

        self._scene.setSceneRect(QRectF(-20, -20, _CARD_W + 40, y + 20))

    def select_response(self, form_id: int) -> None:
        if self._selected_fid is not None and self._selected_fid in self._cards:
            self._cards[self._selected_fid].set_selected(False)
        self._selected_fid = form_id
        card = self._cards.get(form_id)
        if card:
            card.set_selected(True)
            self.ensureVisible(card.sceneBoundingRect(), 30, 30)

    @Slot(int)
    def _on_card_clicked(self, form_id: int) -> None:
        self.select_response(form_id)
        self.response_selected.emit(form_id)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def sizeHint(self) -> QSize:
        return QSize(420, 500)


# ── Background loader ──────────────────────────────────────────────────────────

class _TreeLoader(QObject):
    finished: Signal = Signal(object)   # DialogueTree
    error:    Signal = Signal(str)

    def __init__(self, path: Path, encoding: str) -> None:
        super().__init__()
        self._path     = path
        self._encoding = encoding

    @Slot()
    def run(self) -> None:
        try:
            tree = build_dialogue_tree(self._path, self._encoding)
            self.finished.emit(tree)
        except Exception as exc:
            logger.error("DialogueTree load failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Main dialog ────────────────────────────────────────────────────────────────

class DialogueTreeDialog(QDialog):
    """
    Dialogue Tree Visualizer.

    Left panel: collapsible Quest → Topic → Response tree.
    Right panel: node graph of the selected topic's conversation flow.
    Bottom: full text of the selected response + jump-to-table buttons.

    ``jump_requested(form_id, field_sig)`` is emitted when the user wants to
    navigate the main string table to a specific FormID/field combination.
    """

    jump_requested: Signal = Signal(int, str)   # (form_id, field_sig)

    def __init__(
        self,
        path: Optional[Path] = None,
        encoding: str = "utf-8",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Dialogue Tree Visualizer"))
        self.setMinimumSize(900, 600)
        self.resize(1100, 680)

        self._tree:     Optional[DialogueTree] = None
        self._encoding: str = encoding
        self._path:     Optional[Path] = path
        self._thread:   Optional[QThread] = None
        self._worker:   Optional[_TreeLoader] = None
        self._selected_fid: Optional[int] = None

        self._setup_ui()

        if path and path.exists():
            self._start_load(path, encoding)

    # ── UI construction ────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        self._lbl_file = QLabel(self.tr("No file loaded"))
        self._lbl_file.setStyleSheet("color: #4a6080; font-size: 0.9em;")
        toolbar.addWidget(self._lbl_file, 1)

        btn_open = QPushButton(self.tr("Open ESP/ESM…"))
        btn_open.setFixedWidth(120)
        btn_open.clicked.connect(self._on_open)
        toolbar.addWidget(btn_open)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText(self.tr("Filter quests / topics…"))
        self._filter.setFixedWidth(200)
        self._filter.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self._filter)
        root.addLayout(toolbar)

        # ── Progress bar (hidden while idle) ─────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # ── Main splitter ─────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)

        # Left: hierarchy tree
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        tree_label = QLabel(self.tr("Dialogue Structure"))
        tree_label.setStyleSheet(
            "font-weight: bold; padding: 4px; color: #c0c8d8; background: transparent;"
        )
        left_lay.addWidget(tree_label)
        self._tree_widget = QTreeWidget()
        self._tree_widget.setHeaderHidden(True)
        self._tree_widget.setIndentation(16)
        self._tree_widget.setUniformRowHeights(True)
        self._tree_widget.itemSelectionChanged.connect(self._on_tree_selection)
        self._tree_widget.setStyleSheet("""
            QTreeWidget {
                background: #0a1220;
                color: #c0c8d8;
                border: 1px solid rgba(255,255,255,24);
                selection-background-color: #152030;
                selection-color: #3ff0ff;
            }
            QTreeWidget::item:hover { background: #111c2e; }
            QTreeWidget::item:selected { color: #3ff0ff; background: #152030; }
            QTreeWidget::branch { background: #0a1220; }
        """)
        left_lay.addWidget(self._tree_widget)
        left.setMinimumWidth(240)
        splitter.addWidget(left)

        # Right: graph + detail
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(4)

        self._topic_label = QLabel(self.tr("Select a topic from the tree"))
        self._topic_label.setStyleSheet(
            "font-weight: bold; padding: 4px 4px 2px 4px; color: #3ff0ff;"
        )
        right_lay.addWidget(self._topic_label)

        self._graph = _DialogueGraphView()
        self._graph.response_selected.connect(self._on_response_selected)
        right_lay.addWidget(self._graph, 1)

        # Detail panel
        detail_grp = QGroupBox(self.tr("Selected Response"))
        detail_grp.setStyleSheet("""
            QGroupBox {
                background: #0a1220;
                color: #c0c8d8;
                border: 1px solid rgba(255,255,255,24);
                border-radius: 0px;
                margin-top: 6px;
                padding-top: 4px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 4px;
                color: #3ff0ff;
            }
        """)
        detail_lay = QVBoxLayout(detail_grp)
        detail_lay.setSpacing(4)

        self._txt_detail = QTextBrowser()
        self._txt_detail.setFixedHeight(100)
        self._txt_detail.setOpenLinks(False)
        self._txt_detail.setStyleSheet("""
            QTextBrowser {
                background: #060c18;
                color: #c0c8d8;
                border: 1px solid rgba(255,255,255,18);
                selection-background-color: #1a3050;
            }
        """)
        detail_lay.addWidget(self._txt_detail)

        btn_row = QHBoxLayout()
        self._btn_jump_player = QPushButton(self.tr("Jump to Player Line"))
        self._btn_jump_player.setEnabled(False)
        self._btn_jump_player.setToolTip(self.tr(
            "Navigate the string table to the RNAM field of this INFO record."
        ))
        self._btn_jump_player.clicked.connect(self._jump_player)
        btn_row.addWidget(self._btn_jump_player)

        self._btn_jump_npc = QPushButton(self.tr("Jump to NPC Line"))
        self._btn_jump_npc.setEnabled(False)
        self._btn_jump_npc.setToolTip(self.tr(
            "Navigate the string table to the NAM1 field of this INFO record."
        ))
        self._btn_jump_npc.clicked.connect(self._jump_npc)
        btn_row.addWidget(self._btn_jump_npc)
        btn_row.addStretch()

        detail_lay.addLayout(btn_row)
        right_lay.addWidget(detail_grp)

        splitter.addWidget(right)
        splitter.setSizes([270, 700])
        root.addWidget(splitter, 1)

        # ── Close button ──────────────────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── Loading ────────────────────────────────────────────────────────────────

    def _start_load(self, path: Path, encoding: str) -> None:
        if self._thread and self._thread.isRunning():
            return

        self._progress.setVisible(True)
        self._lbl_file.setText(self.tr("Loading {name}…").format(name=path.name))
        self._tree_widget.clear()

        self._worker = _TreeLoader(path, encoding)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_load_finished)
        self._worker.error.connect(self._on_load_error)
        self._thread.start()

    @Slot(object)
    def _on_load_finished(self, tree: DialogueTree) -> None:
        self._progress.setVisible(False)
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self._tree = tree
        name = self._path.name if self._path else ""
        q_count = len(tree.quests)
        t_count = len(tree.topics)
        r_count = len(tree.responses)
        self._lbl_file.setText(
            self.tr("{name}  —  {q} quests · {t} topics · {r} responses").format(
                name=name, q=q_count, t=t_count, r=r_count,
            )
        )
        self._populate_tree()

    @Slot(str)
    def _on_load_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self._lbl_file.setText(self.tr("Load failed"))
        QMessageBox.critical(self, self.tr("Load Error"), msg)

    # ── Tree population ────────────────────────────────────────────────────────

    def _populate_tree(self) -> None:
        if self._tree is None:
            return
        self._tree_widget.clear()

        # Known quests
        for quest in self._tree.ordered_quests():
            q_item = self._make_quest_item(quest)
            for topic in self._tree.quest_topics(quest.form_id):
                t_item = self._make_topic_item(topic)
                q_item.addChild(t_item)
                for resp in self._tree.topic_response_list(topic.form_id):
                    t_item.addChild(self._make_response_item(resp))
            self._tree_widget.addTopLevelItem(q_item)

        # Orphan topics (QNAM not in file)
        orphans = self._tree.orphan_topics()
        if orphans:
            no_quest = QTreeWidgetItem([self.tr("(No Quest / Unlinked)")])
            no_quest.setData(0, _ROLE_KIND, "group")
            no_quest.setForeground(0, QColor("#4a6080"))
            for topic in orphans:
                t_item = self._make_topic_item(topic)
                no_quest.addChild(t_item)
                for resp in self._tree.topic_response_list(topic.form_id):
                    t_item.addChild(self._make_response_item(resp))
            self._tree_widget.addTopLevelItem(no_quest)

    def _make_quest_item(self, quest: QuestNode) -> QTreeWidgetItem:
        label = f"[Q] {quest.name}"
        item  = QTreeWidgetItem([label])
        item.setData(0, _ROLE_KIND, "quest")
        item.setData(0, _ROLE_FORM_ID, quest.form_id)
        item.setForeground(0, QColor("#3fb0ff"))
        item.setToolTip(0, f"0x{quest.form_id:08X}  EDID: {quest.edid or '—'}")
        return item

    def _make_topic_item(self, topic: TopicNode) -> QTreeWidgetItem:
        label = f"[T] {topic.name}"
        item  = QTreeWidgetItem([label])
        item.setData(0, _ROLE_KIND, "topic")
        item.setData(0, _ROLE_FORM_ID, topic.form_id)
        item.setForeground(0, QColor("#3fd8b0"))
        item.setToolTip(0, f"0x{topic.form_id:08X}  EDID: {topic.edid or '—'}")
        return item

    def _make_response_item(self, resp: ResponseNode) -> QTreeWidgetItem:
        preview = _trunc(resp.npc_line or resp.player_prompt or "—", 60)
        label   = f"[R] {preview}"
        item    = QTreeWidgetItem([label])
        item.setData(0, _ROLE_KIND, "response")
        item.setData(0, _ROLE_FORM_ID, resp.form_id)
        item.setForeground(0, QColor("#c0c8d8"))
        tip = (
            f"0x{resp.form_id:08X}  EDID: {resp.edid or '—'}\n"
            f"Player: {_trunc(resp.player_prompt, 80) or '—'}\n"
            f"NPC:    {_trunc(resp.npc_line, 80) or '—'}"
        )
        item.setToolTip(0, tip)
        return item

    # ── Filter ─────────────────────────────────────────────────────────────────

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        root = self._tree_widget.invisibleRootItem()
        for qi in range(root.childCount()):
            q_item = root.child(qi)
            q_visible = False
            for ti in range(q_item.childCount()):
                t_item = q_item.child(ti)
                t_visible = False
                for ri in range(t_item.childCount()):
                    r_item = t_item.child(ri)
                    match  = not text or text in r_item.text(0).lower()
                    r_item.setHidden(not match)
                    if match:
                        t_visible = True
                t_match = not text or text in t_item.text(0).lower()
                t_item.setHidden(not (t_match or t_visible))
                if t_match or t_visible:
                    q_visible = True
            q_match = not text or text in q_item.text(0).lower()
            q_item.setHidden(not (q_match or q_visible))

    # ── Tree selection ─────────────────────────────────────────────────────────

    @Slot()
    def _on_tree_selection(self) -> None:
        items = self._tree_widget.selectedItems()
        if not items:
            return
        item = items[0]
        kind    = item.data(0, _ROLE_KIND)
        form_id = item.data(0, _ROLE_FORM_ID)

        if kind == "topic" and self._tree:
            topic = self._tree.topics.get(form_id)
            self._topic_label.setText(
                f"{topic.name}  (0x{form_id:08X})" if topic else ""
            )
            responses = self._tree.topic_response_list(form_id)
            self._graph.load_responses(responses)
            self._clear_detail()

        elif kind == "response" and self._tree:
            resp = self._tree.responses.get(form_id)
            if resp:
                self._graph.select_response(form_id)
                self._update_detail(resp)
            # Also select the parent topic in the graph if not already loaded
            parent = item.parent()
            if parent and parent.data(0, _ROLE_KIND) == "topic":
                t_fid = parent.data(0, _ROLE_FORM_ID)
                topic = self._tree.topics.get(t_fid)
                self._topic_label.setText(
                    f"{topic.name}  (0x{t_fid:08X})" if topic else ""
                )
                # Load the topic if not already showing its cards
                if form_id not in self._graph._cards:
                    responses = self._tree.topic_response_list(t_fid)
                    self._graph.load_responses(responses)
                    self._graph.select_response(form_id)

        elif kind == "quest":
            self._graph.load_responses([])
            self._clear_detail()
            self._topic_label.setText(self.tr("Select a topic from the tree"))

    # ── Graph response click ───────────────────────────────────────────────────

    @Slot(int)
    def _on_response_selected(self, form_id: int) -> None:
        self._selected_fid = form_id
        if self._tree:
            resp = self._tree.responses.get(form_id)
            if resp:
                self._update_detail(resp)
        # Mirror selection in the tree widget
        self._select_tree_item("response", form_id)

    # ── Detail panel ───────────────────────────────────────────────────────────

    def _update_detail(self, resp: ResponseNode) -> None:
        self._selected_fid = resp.form_id
        lines = [
            f"<b>FormID:</b> 0x{resp.form_id:08X}"
            + (f"  <span style='color:#4a6080'>{resp.edid}</span>" if resp.edid else ""),
        ]
        if resp.player_prompt:
            lines.append(
                f"<p><span style='color:#3fb0ff'><b>Player:</b></span> "
                f"{_html_escape(resp.player_prompt)}</p>"
            )
        if resp.npc_line:
            lines.append(
                f"<p><span style='color:#3ff0ff'><b>NPC:</b></span> "
                f"{_html_escape(resp.npc_line)}</p>"
            )
        self._txt_detail.setHtml("".join(lines))
        self._btn_jump_player.setEnabled(bool(resp.player_prompt))
        self._btn_jump_npc.setEnabled(bool(resp.npc_line))

    def _clear_detail(self) -> None:
        self._selected_fid = None
        self._txt_detail.clear()
        self._btn_jump_player.setEnabled(False)
        self._btn_jump_npc.setEnabled(False)

    # ── Jump buttons ───────────────────────────────────────────────────────────

    @Slot()
    def _jump_player(self) -> None:
        if self._selected_fid is not None:
            self.jump_requested.emit(self._selected_fid, "RNAM")

    @Slot()
    def _jump_npc(self) -> None:
        if self._selected_fid is not None:
            self.jump_requested.emit(self._selected_fid, "NAM1")

    # ── Open button ────────────────────────────────────────────────────────────

    @Slot()
    def _on_open(self) -> None:
        path_str, _ = get_open_filename(
            self,
            self.tr("Open ESP/ESM File"),
            filter=self.tr("Plugin Files (*.esp *.esm *.esl);;All Files (*)"),
        )
        if not path_str:
            return
        path = Path(path_str)
        self._path = path
        self._start_load(path, self._encoding)

    # ── Tree widget sync ───────────────────────────────────────────────────────

    def _select_tree_item(self, kind: str, form_id: int) -> None:
        """Highlight the tree item matching kind/form_id without re-triggering load."""
        root = self._tree_widget.invisibleRootItem()
        result = _find_tree_item(root, kind, form_id)
        if result:
            self._tree_widget.blockSignals(True)
            self._tree_widget.setCurrentItem(result)
            self._tree_widget.scrollToItem(result)
            self._tree_widget.blockSignals(False)

    # ── Window close guard ─────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self._thread and self._thread.isRunning():
            event.ignore()
            QMessageBox.information(
                self, self.tr("Loading"),
                self.tr("Please wait for the file to finish loading."),
            )
            return
        super().closeEvent(event)


# ── Module-level helpers ───────────────────────────────────────────────────────

def _find_tree_item(
    parent: QTreeWidgetItem,
    kind: str,
    form_id: int,
) -> Optional[QTreeWidgetItem]:
    for i in range(parent.childCount()):
        child = parent.child(i)
        if child.data(0, _ROLE_KIND) == kind and child.data(0, _ROLE_FORM_ID) == form_id:
            return child
        found = _find_tree_item(child, kind, form_id)
        if found:
            return found
    return None


def _html_escape(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
