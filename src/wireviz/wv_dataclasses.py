# -*- coding: utf-8 -*-

from collections import namedtuple
from dataclasses import dataclass, field
from enum import Enum
from itertools import zip_longest
from typing import Dict, List, Optional, Tuple, Union

from wireviz.wv_bom import BomHash, BomHashList, PartNumberInfo
from wireviz.wv_colors import (
    COLOR_CODES,
    ColorOutputMode,
    MultiColor,
    SingleColor,
    get_color_by_colorcode_index,
)
from wireviz.wv_utils import aspect_ratio, awg_equiv, mm2_equiv, remove_links

# Each type alias have their legal values described in comments
# - validation might be implemented in the future
PlainText = str  # Text not containing HTML tags nor newlines
Hypertext = str  # Text possibly including HTML hyperlinks that are removed in all outputs except HTML output
MultilineHypertext = (
    str  # Hypertext possibly also including newlines to break lines in diagram output
)

Designator = PlainText  # Case insensitive unique name of connector or cable

# Literal type aliases below are commented to avoid requiring python 3.8
ConnectorMultiplier = PlainText  # = Literal['pincount', 'populated']
CableMultiplier = (
    PlainText  # = Literal['wirecount', 'terminations', 'length', 'total_length']
)
ImageScale = PlainText  # = Literal['false', 'true', 'width', 'height', 'both']

# Type combinations
Pin = Union[int, PlainText]  # Pin identifier
PinIndex = int  # Zero-based pin index
Wire = Union[int, PlainText]  # Wire number or Literal['s'] for shield
NoneOrMorePins = Union[
    Pin, Tuple[Pin, ...], None
]  # None, one, or a tuple of pin identifiers
NoneOrMorePinIndices = Union[
    PinIndex, Tuple[PinIndex, ...], None
]  # None, one, or a tuple of zero-based pin indices
OneOrMoreWires = Union[Wire, Tuple[Wire, ...]]  # One or a tuple of wires

# Metadata can contain whatever is needed by the HTML generation/template.
MetadataKeys = PlainText  # Literal['title', 'description', 'notes', ...]


Side = Enum("Side", "LEFT RIGHT")
ArrowDirection = Enum("ArrowDirection", "NONE BACK FORWARD BOTH")
ArrowWeight = Enum("ArrowWeight", "SINGLE DOUBLE")

AUTOGENERATED_PREFIX = "AUTOGENERATED_"


@dataclass
class Arrow:
    direction: ArrowDirection
    weight: ArrowWeight


class Metadata(dict):
    pass


@dataclass
class Options:
    fontname: PlainText = "arial"
    bgcolor: SingleColor = "WH"  # will be converted to SingleColor in __post_init__
    bgcolor_node: SingleColor = "WH"
    bgcolor_connector: SingleColor = None
    bgcolor_cable: SingleColor = None
    bgcolor_bundle: SingleColor = None
    color_mode: ColorOutputMode = ColorOutputMode.EN_UPPER
    mini_bom_mode: bool = True
    template_separator: str = "."
    _pad: int = 0
    # TODO: resolve template and image paths during rendering, not during YAML parsing
    _template_paths: [List] = field(default_factory=list)
    _image_paths: [List] = field(default_factory=list)

    def __post_init__(self):

        self.bgcolor = SingleColor(self.bgcolor)
        self.bgcolor_node = SingleColor(self.bgcolor_node)
        self.bgcolor_connector = SingleColor(self.bgcolor_connector)
        self.bgcolor_cable = SingleColor(self.bgcolor_cable)
        self.bgcolor_bundle = SingleColor(self.bgcolor_bundle)

        if not self.bgcolor_node:
            self.bgcolor_node = self.bgcolor
        if not self.bgcolor_connector:
            self.bgcolor_connector = self.bgcolor_node
        if not self.bgcolor_cable:
            self.bgcolor_cable = self.bgcolor_node
        if not self.bgcolor_bundle:
            self.bgcolor_bundle = self.bgcolor_cable


@dataclass
class Tweak:
    override: Optional[Dict[Designator, Dict[str, Optional[str]]]] = None
    append: Union[str, List[str], None] = None


@dataclass
class Image:
    # Attributes of the image object <img>:
    src: str
    scale: Optional[ImageScale] = None
    # Attributes of the image cell <td> containing the image:
    width: Optional[int] = None
    height: Optional[int] = None
    fixedsize: Optional[bool] = None
    bgcolor: SingleColor = None
    # Contents of the text cell <td> just below the image cell:
    caption: Optional[MultilineHypertext] = None
    # See also HTML doc at https://graphviz.org/doc/info/shapes.html#html

    def __post_init__(self):

        self.bgcolor = SingleColor(self.bgcolor)

        if self.fixedsize is None:
            # Default True if any dimension specified unless self.scale also is specified.
            self.fixedsize = (self.width or self.height) and self.scale is None

        if self.scale is None:
            if not self.width and not self.height:
                self.scale = "false"
            elif self.width and self.height:
                self.scale = "both"
            else:
                self.scale = "true"  # When only one dimension is specified.

        if self.fixedsize:
            # If only one dimension is specified, compute the other
            # because Graphviz requires both when fixedsize=True.
            if self.height:
                if not self.width:
                    self.width = self.height * aspect_ratio(self.src)
            else:
                if self.width:
                    self.height = self.width / aspect_ratio(self.src)


@dataclass
class PinClass:
    index: int
    id: str
    label: str
    color: MultiColor
    parent: str  # designator of parent connector
    _anonymous: bool = False  # true for pins on autogenerated connectors
    _simple: bool = False  # true for simple connector

    def __str__(self):
        snippets = [  # use str() for each in case they are int or other non-str
            str(self.parent) if not self._anonymous else "",
            str(self.id) if not self._anonymous and not self._simple else "",
            str(self.label) if self.label else "",
        ]
        return ":".join([snip for snip in snippets if snip != ""])


@dataclass
class WireClass:
    index: int
    id: str
    label: str
    color: MultiColor
    parent: str  # designator of parent cable/bundle
    # gauge: Gauge
    # pn: str
    # manufacturer: str
    # mpn: str
    # supplier: str
    # spn: str


@dataclass
class ShieldClass(WireClass):
    pass  # TODO, for wires with multiple shields more shield details, ...


@dataclass
class Connection:
    from_: PinClass = None
    via: Union[WireClass, ShieldClass] = None
    to: PinClass = None


@dataclass
class Component:
    category: Optional[str] = None  # currently only used by cables, to define bundles
    type: Union[MultilineHypertext, List[MultilineHypertext]] = None
    subtype: Union[MultilineHypertext, List[MultilineHypertext]] = None

    # part number
    partnumbers: PartNumberInfo = None  # filled by fill_partnumbers()
    # the following are provided for user convenience and should not be accessed later.
    # their contents are loaded into partnumbers during the child class __post_init__()
    pn: str = None
    manufacturer: str = None
    mpn: str = None
    supplier: str = None
    spn: str = None

    ignore_in_bom: bool = False
    bom_id: Optional[str] = None  # to be filled after harness is built

    def fill_partnumbers(self):
        partnos = [self.pn, self.manufacturer, self.mpn, self.supplier, self.spn]
        partnos = [remove_links(entry) for entry in partnos]
        partnos = tuple(partnos)
        self.partnumbers = PartNumberInfo(*partnos)

    @property
    def bom_hash(self) -> BomHash:
        def _force_list(inp):
            if isinstance(inp, list):
                return inp
            else:
                return [inp for i in range(len(self.colors))]

        if self.category == "bundle":
            # create a temporary single item that includes the necessary fields,
            # which may or may not be lists
            _hash_list = BomHashList(
                self.description,
                self.unit,
                self.partnumbers,
            )
            # convert elements that are not lists, into lists
            _hash_matrix = list(map(_force_list, [elem for elem in _hash_list]))
            # transpose list of lists, convert to tuple for next step
            _hash_matrix = list(map(tuple, zip(*_hash_matrix)))
            # generate list of BomHashes
            hash_list = [BomHash(*item) for item in _hash_matrix]
            return hash_list
        else:
            return BomHash(
                self.description,
                self.unit,
                self.partnumbers,
            )


@dataclass
class AdditionalComponent(Component):
    qty: float = 1
    unit: Optional[str] = None
    qty_multiplier: Union[ConnectorMultiplier, CableMultiplier, None] = 1
    designators: Optional[str] = None  # used for components definedi in the
    #                                    additional_bom_items section within another component
    bgcolor: SingleColor = None  #       ^ same here

    def __post_init__(self):
        super().fill_partnumbers()
        self.bgcolor = SingleColor(self.bgcolor)

    @property
    def qty_final(self):
        return 999

    @property
    def description(self) -> str:
        substrs = [self.type, self.subtype if self.subtype else ""]
        return ", ".join(substrs)


@dataclass
class GraphicalComponent(Component):  # abstract class, for future use
    bgcolor: Optional[SingleColor] = None


@dataclass
class TopLevelGraphicalComponent(GraphicalComponent):  # abstract class
    # component properties
    designator: Designator = None
    color: Optional[SingleColor] = None
    image: Optional[Image] = None
    additional_components: List[AdditionalComponent] = field(default_factory=list)
    notes: Optional[MultilineHypertext] = None
    # rendering options
    bgcolor_title: Optional[SingleColor] = None
    show_name: Optional[bool] = None


@dataclass
class Connector(TopLevelGraphicalComponent):
    # connector-specific properties
    style: Optional[str] = None
    category: Optional[str] = None
    loops: List[List[Pin]] = field(default_factory=list)
    # pin information in particular
    pincount: Optional[int] = None
    pins: List[Pin] = field(default_factory=list)  # legacy
    pinlabels: List[Pin] = field(default_factory=list)  # legacy
    pincolors: List[str] = field(default_factory=list)  # legacy
    pin_objects: List[PinClass] = field(
        default_factory=list
    )  # new, to replace the lists above
    # rendering option
    show_pincount: Optional[bool] = None
    hide_disconnected_pins: bool = False

    @property
    def is_autogenerated(self):
        return self.designator.startswith(AUTOGENERATED_PREFIX)

    @property
    def description(self) -> str:
        substrs = [
            "Connector",
            self.type,
            self.subtype,
            f"{self.pincount} pins" if self.show_pincount else None,
            str(self.color) if self.color else None,
        ]
        return ", ".join([str(s) for s in substrs if s is not None and s != ""])

    def should_show_pin(self, pin_name):
        return not self.hide_disconnected_pins or self.visible_pins.get(pin_name, False)

    @property
    def unit(self):  # for compatibility with BOM hashing
        return None  # connectors do not support units.

    def __post_init__(self) -> None:

        super().fill_partnumbers()

        self.bgcolor = SingleColor(self.bgcolor)
        self.bgcolor_title = SingleColor(self.bgcolor_title)
        self.color = SingleColor(self.color)

        if isinstance(self.image, dict):
            self.image = Image(**self.image)

        self.ports_left = False
        self.ports_right = False
        self.visible_pins = {}

        if self.style == "simple":
            if self.pincount and self.pincount > 1:
                raise Exception(
                    "Connectors with style set to simple may only have one pin"
                )
            self.pincount = 1

        if not self.pincount:
            self.pincount = max(
                len(self.pins), len(self.pinlabels), len(self.pincolors)
            )
            if not self.pincount:
                raise Exception(
                    "You need to specify at least one: "
                    "pincount, pins, pinlabels, or pincolors"
                )

        # create default list for pins (sequential) if not specified
        if not self.pins:
            self.pins = list(range(1, self.pincount + 1))

        if len(self.pins) != len(set(self.pins)):
            raise Exception("Pins are not unique")

        # all checks have passed
        pin_tuples = zip_longest(
            self.pins,
            self.pinlabels,
            self.pincolors,
        )
        for pin_index, (pin_id, pin_label, pin_color) in enumerate(pin_tuples):
            self.pin_objects.append(
                PinClass(
                    index=pin_index,
                    id=pin_id,
                    label=pin_label,
                    color=MultiColor(pin_color),
                    parent=self.designator,
                    _anonymous=self.is_autogenerated,
                    _simple=self.style == "simple",
                )
            )

        if self.show_name is None:
            self.show_name = self.style != "simple" and not self.is_autogenerated

        if self.show_pincount is None:
            # hide pincount for simple (1 pin) connectors by default
            self.show_pincount = self.style != "simple"

        for loop in self.loops:
            # TODO: check that pins to connect actually exist
            # TODO: allow using pin labels in addition to pin numbers,
            #       just like when defining regular connections
            # TODO: include properties of wire used to create the loop
            if len(loop) != 2:
                raise Exception("Loops must be between exactly two pins!")

        for i, item in enumerate(self.additional_components):
            if isinstance(item, dict):
                self.additional_components[i] = AdditionalComponent(**item)

    def _check_if_unique_id(self, id):
        results = [pin for pin in self.pin_objects if pin.id == id]
        if len(results) == 0:
            raise Exception(f"Pin ID {id} not found in {self.designator}")
        if len(results) > 1:
            raise Exception(f"Pin ID {id} found more than once in {self.designator}")
        return True

    def get_pin_by_id(self, id):
        if self._check_if_unique_id(id):
            pin = [pin for pin in self.pin_objects if pin.id == id]
            return pin[0]

    def activate_pin(self, pin: Pin, side: Side) -> None:
        self.visible_pins[pin] = True
        if side == Side.LEFT:
            self.ports_left = True
        elif side == Side.RIGHT:
            self.ports_right = True

    def get_qty_multiplier(self, qty_multiplier: Optional[ConnectorMultiplier]) -> int:
        # TODO!!! how and when to compute final qty for additional components???
        if not qty_multiplier:
            return 1
        elif qty_multiplier == "pincount":
            return self.pincount
        elif qty_multiplier == "populated":
            return sum(self.visible_pins.values())
        else:
            raise ValueError(
                f"invalid qty multiplier parameter for connector {qty_multiplier}"
            )


@dataclass
class Cable(TopLevelGraphicalComponent):
    # cable-specific properties
    gauge: Optional[float] = None
    gauge_unit: Optional[str] = None
    length: float = 0
    length_unit: Optional[str] = None
    color_code: Optional[str] = None
    # wire information in particular
    wirecount: Optional[int] = None
    shield: Union[bool, MultiColor] = False
    colors: List[str] = field(default_factory=list)  # legacy
    wirelabels: List[Wire] = field(default_factory=list)  # legacy
    wire_objects: List[WireClass] = field(
        default_factory=list
    )  # new, to replace the lists above
    # internal
    _connections: List[Connection] = field(default_factory=list)
    # rendering options
    show_name: Optional[bool] = None
    show_equiv: bool = False
    show_wirecount: bool = True
    show_wirenumbers: Optional[bool] = None

    @property
    def is_autogenerated(self):
        return self.designator.startswith(AUTOGENERATED_PREFIX)

    @property
    def unit(self):  # for compatibility with parent class
        return self.length_unit

    @property
    def gauge_str(self):
        if not self.gauge:
            return None
        actual_gauge = f"{self.gauge} {self.gauge_unit}"
        equivalent_gauge = ""
        if self.show_equiv:
            # Only convert units we actually know about, i.e. currently
            # mm2 and awg --- other units _are_ technically allowed,
            # and passed through as-is.
            if self.gauge_unit == "mm\u00B2":
                equivalent_gauge = f" ({awg_equiv(self.gauge)} AWG)"
            elif self.gauge_unit.upper() == "AWG":
                equivalent_gauge = f" ({mm2_equiv(self.gauge)} mm\u00B2)"
        return f"{actual_gauge}{equivalent_gauge}"

    @property
    def description(self) -> str:
        if self.category == "bundle":
            desc_list = []
            for index, color in enumerate(self.colors):
                substrs = [
                    "Wire",
                    self.type,
                    self.subtype,
                    f"{self.gauge} {self.gauge_unit}" if self.gauge else None,
                    str(self.color)
                    if self.color
                    else None,  # translate_color(self.color, harness.options.color_mode)] <- get harness.color_mode!
                ]
                desc_list.append(
                    ", ".join([s for s in substrs if s is not None and s != ""])
                )
            return desc_list
        else:
            substrs = [
                ("", "Cable"),
                (", ", self.type),
                (", ", self.subtype),
                (", ", self.wirecount),
                (" ", f"x {self.gauge} {self.gauge_unit}" if self.gauge else " wires"),
                (" ", "shielded" if self.shield else None),
                (", ", str(self.color) if self.color else None),
            ]
            desc = "".join(
                [f"{s[0]}{s[1]}" for s in substrs if s[1] is not None and s[1] != ""]
            )
            return desc

    def __post_init__(self) -> None:

        super().fill_partnumbers()

        self.bgcolor = SingleColor(self.bgcolor)
        self.bgcolor_title = SingleColor(self.bgcolor_title)
        self.color = SingleColor(self.color)

        if isinstance(self.image, dict):
            self.image = Image(**self.image)

        if isinstance(self.gauge, str):  # gauge and unit specified
            try:
                g, u = self.gauge.split(" ")
            except Exception:
                raise Exception(
                    f"Cable {self.designator} gauge={self.gauge} - "
                    "Gauge must be a number, or number and unit separated by a space"
                )
            self.gauge = g

            if self.gauge_unit is not None:
                print(
                    f"Warning: Cable {self.designator} gauge_unit={self.gauge_unit} "
                    f"is ignored because its gauge contains {u}"
                )
            if u.upper() == "AWG":
                self.gauge_unit = u.upper()
            else:
                self.gauge_unit = u.replace("mm2", "mm\u00B2")

        elif self.gauge is not None:  # gauge specified, assume mm2
            if self.gauge_unit is None:
                self.gauge_unit = "mm\u00B2"
        else:
            pass  # gauge not specified

        if isinstance(self.length, str):  # length and unit specified
            try:
                L, u = self.length.split(" ")
                L = float(L)
            except Exception:
                raise Exception(
                    f"Cable {self.designator} length={self.length} - "
                    "Length must be a number, or number and unit separated by a space"
                )
            self.length = L
            if self.length_unit is not None:
                print(
                    f"Warning: Cable {self.designator} length_unit={self.length_unit} is ignored "
                    f"because its length contains {u}"
                )
            self.length_unit = u
        elif not any(isinstance(self.length, t) for t in [int, float]):
            raise Exception(f"Cable {self.designator} length has a non-numeric value")
        elif self.length_unit is None:
            self.length_unit = "m"

        if self.wirecount:  # number of wires explicitly defined
            if self.colors:  # use custom color palette (partly or looped if needed)
                self.colors = [
                    self.colors[i % len(self.colors)] for i in range(self.wirecount)
                ]
            elif self.color_code:
                # use standard color palette (partly or looped if needed)
                if self.color_code not in COLOR_CODES:
                    raise Exception("Unknown color code")
                self.colors = [
                    get_color_by_colorcode_index(self.color_code, i)
                    for i in range(self.wirecount)
                ]
            else:  # no colors defined, add dummy colors
                self.colors = [""] * self.wirecount

        else:  # wirecount implicit in length of color list
            if not self.colors:
                raise Exception(
                    "Unknown number of wires. "
                    "Must specify wirecount or colors (implicit length)"
                )
            self.wirecount = len(self.colors)

        if self.wirelabels:
            if self.shield and "s" in self.wirelabels:
                raise Exception(
                    '"s" may not be used as a wire label for a shielded cable.'
                )

        # if lists of part numbers are provided,
        # check this is a bundle and that it matches the wirecount.
        for idfield in [self.manufacturer, self.mpn, self.supplier, self.spn, self.pn]:
            if isinstance(idfield, list):
                if self.category == "bundle":
                    # check the length
                    if len(idfield) != self.wirecount:
                        raise Exception("lists of part data must match wirecount")
                else:
                    raise Exception("lists of part data are only supported for bundles")

        # all checks have passed
        wire_tuples = zip_longest(
            # TODO: self.wire_ids
            self.colors,
            self.wirelabels,
        )
        for wire_index, (wire_color, wire_label) in enumerate(wire_tuples):
            self.wire_objects.append(
                WireClass(
                    index=wire_index,  # TODO: wire_id
                    id=wire_index + 1,  # TODO: wire_id
                    label=wire_label,
                    color=MultiColor(wire_color),
                    parent=self.designator,
                )
            )

        if self.shield:
            index_offset = len(self.wire_objects)
            # TODO: add support for multiple shields
            self.wire_objects.append(
                ShieldClass(
                    index=index_offset,
                    id="s",
                    label="Shield",
                    color=MultiColor(self.shield)
                    if isinstance(self.shield, str)
                    else MultiColor(None),
                    parent=self.designator,
                )
            )

        if self.show_name is None:
            self.show_name = not self.is_autogenerated

        if not self.show_wirenumbers:
            # by default, show wire numbers for cables, hide for bundles
            self.show_wirenumbers = self.category != "bundle"

        for i, item in enumerate(self.additional_components):
            if isinstance(item, dict):
                self.additional_components[i] = AdditionalComponent(**item)

    def get_wire_by_id(self, id):
        wire = [wire for wire in self.wire_objects if wire.id == id]
        if len(wire) == 0:
            raise Exception(f"Wire ID {id} not found in {self.designator}")
        if len(wire) > 1:
            raise Exception(f"Wire ID {id} found more than once in {self.designator}")
        return wire[0]

    def _connect(
        self,
        from_pin_obj: [PinClass],
        via_wire_id: str,
        to_pin_obj: [PinClass],
    ) -> None:
        via_wire_obj = self.get_wire_by_id(via_wire_id)
        self._connections.append(Connection(from_pin_obj, via_wire_obj, to_pin_obj))

    def get_qty_multiplier(self, qty_multiplier: Optional[CableMultiplier]) -> float:
        if not qty_multiplier:
            return 1
        elif qty_multiplier == "wirecount":
            return self.wirecount
        elif qty_multiplier == "terminations":
            return len(self.connections)
        elif qty_multiplier == "length":
            return self.length
        elif qty_multiplier == "total_length":
            return self.length * self.wirecount
        else:
            raise ValueError(
                f"invalid qty multiplier parameter for cable {qty_multiplier}"
            )


@dataclass
class MatePin:
    from_: PinClass
    to: PinClass
    arrow: Arrow


@dataclass
class MateComponent:
    from_: str  # Designator
    to: str  # Designator
    arrow: Arrow