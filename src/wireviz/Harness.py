# -*- coding: utf-8 -*-

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from graphviz import Graph

import wireviz.wv_colors
from wireviz.DataClasses import (
    AUTOGENERATED_PREFIX,
    AdditionalComponent,
    Arrow,
    ArrowWeight,
    Cable,
    Component,
    Connector,
    MateComponent,
    MatePin,
    Metadata,
    Options,
    Side,
    Tweak,
)
from wireviz.svgembed import embed_svg_images_file
from wireviz.wv_gv_html import (
    apply_dot_tweaks,
    calculate_node_bgcolor,
    gv_connector_loops,
    gv_edge_mate,
    gv_edge_wire,
    gv_node_component,
    parse_arrow_str,
    set_dot_basics,
)
from wireviz.wv_helper import open_file_write, tuplelist2tsv
from wireviz.wv_html import generate_html_output


@dataclass
class Harness:
    metadata: Metadata
    options: Options
    tweak: Tweak
    additional_bom_items: List[AdditionalComponent] = field(default_factory=list)

    def __post_init__(self):
        self.connectors = {}
        self.cables = {}
        self.mates = []
        self._bom = defaultdict(dict)
        self.additional_bom_items = []

    def add_connector(self, designator: str, *args, **kwargs) -> None:
        conn = Connector(designator=designator, *args, **kwargs)
        self.connectors[designator] = conn
        # self._add_to_internal_bom(conn)

    def add_cable(self, designator: str, *args, **kwargs) -> None:
        cbl = Cable(designator=designator, *args, **kwargs)
        self.cables[designator] = cbl
        # self._add_to_internal_bom(cbl)

    def add_additional_bom_item(self, item: dict) -> None:
        new_item = AdditionalComponent(**item)
        self.additional_bom_items.append(new_item)
        # self._add_to_internal_bom(new_item)

    def add_mate_pin(self, from_name, from_pin, to_name, to_pin, arrow_str) -> None:
        from_con = self.connectors[from_name]
        from_pin_obj = from_con.get_pin_by_id(from_pin)
        to_con = self.connectors[to_name]
        to_pin_obj = to_con.get_pin_by_id(to_pin)
        arrow = Arrow(direction=parse_arrow_str(arrow_str), weight=ArrowWeight.SINGLE)

        self.mates.append(MatePin(from_pin_obj, to_pin_obj, arrow))
        self.connectors[from_name].activate_pin(from_pin, Side.RIGHT)
        self.connectors[to_name].activate_pin(to_pin, Side.LEFT)

    def add_mate_component(self, from_name, to_name, arrow_str) -> None:
        arrow = Arrow(direction=parse_arrow_str(arrow_str), weight=ArrowWeight.SINGLE)
        self.mates.append(MateComponent(from_name, to_name, arrow))

    def _add_to_internal_bom(self, item: Component):
        if item.ignore_in_bom:
            return

        def _add(hash, designator=None, qty=1, category=None):
            # generate entry
            bom_entry = self._bom[hash]
            # initialize missing fields
            if not "qty" in bom_entry:
                bom_entry["qty"] = 0
            if not "designators" in bom_entry:
                bom_entry["designators"] = set()
            # update fields
            bom_entry["qty"] += qty
            if designator is None:
                designator_list = []
            elif isinstance(designator, list):
                designator_list = designator
            else:
                designator_list = [designator]
            for des in designator_list:
                if des and not des.startswith(AUTOGENERATED_PREFIX):
                    bom_entry["designators"].add(des)
            bom_entry["category"] = category

        if isinstance(item, Connector):
            _add(item.bom_hash, designator=item.designator, category="connector")
            for comp in item.additional_components:
                if comp.ignore_in_bom:
                    continue
                _add(
                    comp.bom_hash,
                    designator=item.designator,
                    qty=comp.qty_final,
                    category="connector/additional",
                )
        elif isinstance(item, Cable):
            _bom_hash = item.bom_hash
            if item.category == "bundle":
                _cat = "wire"
                for wire in item.wire_objects:
                    _add(
                        None,
                        qty=item.length + 0.001,
                        designator=item.designator,
                        category="wire DUMMY",
                    )
            else:
                _cat = "cable"
                _add(
                    item.bom_hash,
                    qty=item.length,
                    designator=item.designator,
                    category="cable",
                )
            for comp in item.additional_components:
                if comp.ignore_in_bom:
                    continue
                _add(
                    comp.bom_hash,
                    designator=item.designator,
                    qty=comp.qty_final,
                    category=f"{_cat}/additional",
                )
        elif isinstance(item, AdditionalComponent):  # additional component
            _add(
                item.bom_hash,
                designator=item.designators,
                qty=item.qty_final,
                category="additional",
            )
        else:
            raise Exception(f"Unknown type of item:\n{item}")

    def populate_bom(self):
        pass
        # raise Exception(
        #     "Implement BOM population after all connections have been made, "
        #     " so that additional component qty's can be computed correctly "
        #     "(factoring in number of connected pins/wires/...)"
        #     )

    def connect(
        self,
        from_name: str,
        from_pin: (int, str),
        via_name: str,
        via_wire: (int, str),
        to_name: str,
        to_pin: (int, str),
    ) -> None:
        # check from and to connectors
        for (name, pin) in zip([from_name, to_name], [from_pin, to_pin]):
            if name is not None and name in self.connectors:
                connector = self.connectors[name]
                # check if provided name is ambiguous
                if pin in connector.pins and pin in connector.pinlabels:
                    if connector.pins.index(pin) != connector.pinlabels.index(pin):
                        raise Exception(
                            f"{name}:{pin} is defined both in pinlabels and pins, "
                            "for different pins."
                        )
                    # TODO: Maybe issue a warning if present in both lists
                    # but referencing the same pin?
                if pin in connector.pinlabels:
                    if connector.pinlabels.count(pin) > 1:
                        raise Exception(f"{name}:{pin} is defined more than once.")
                    index = connector.pinlabels.index(pin)
                    pin = connector.pins[index]  # map pin name to pin number
                    if name == from_name:
                        from_pin = pin
                    if name == to_name:
                        to_pin = pin
                if not pin in connector.pins:
                    raise Exception(f"{name}:{pin} not found.")

        # check via cable
        if via_name in self.cables:
            cable = self.cables[via_name]
            # check if provided name is ambiguous
            if via_wire in cable.colors and via_wire in cable.wirelabels:
                if cable.colors.index(via_wire) != cable.wirelabels.index(via_wire):
                    raise Exception(
                        f"{via_name}:{via_wire} is defined both in colors and wirelabels, "
                        "for different wires."
                    )
                # TODO: Maybe issue a warning if present in both lists
                # but referencing the same wire?
            if via_wire in cable.colors:
                if cable.colors.count(via_wire) > 1:
                    raise Exception(
                        f"{via_name}:{via_wire} is used for more than one wire."
                    )
                # list index starts at 0, wire IDs start at 1
                via_wire = cable.colors.index(via_wire) + 1
            elif via_wire in cable.wirelabels:
                if cable.wirelabels.count(via_wire) > 1:
                    raise Exception(
                        f"{via_name}:{via_wire} is used for more than one wire."
                    )
                via_wire = (
                    cable.wirelabels.index(via_wire) + 1
                )  # list index starts at 0, wire IDs start at 1

        # perform the actual connection
        if from_name is not None:
            from_con = self.connectors[from_name]
            from_pin_obj = from_con.get_pin_by_id(from_pin)
        else:
            from_pin_obj = None
        if to_name is not None:
            to_con = self.connectors[to_name]
            to_pin_obj = to_con.get_pin_by_id(to_pin)
        else:
            to_pin_obj = None

        self.cables[via_name]._connect(from_pin_obj, via_wire, to_pin_obj)
        if from_name in self.connectors:
            self.connectors[from_name].activate_pin(from_pin, Side.RIGHT)
        if to_name in self.connectors:
            self.connectors[to_name].activate_pin(to_pin, Side.LEFT)

    def create_graph(self) -> Graph:
        dot = Graph()
        set_dot_basics(dot, self.options)

        for connector in self.connectors.values():
            # generate connector node
            gv_html = gv_node_component(connector)
            bgcolor = calculate_node_bgcolor(connector, self.options)
            dot.node(
                connector.designator,
                label=f"<\n{gv_html}\n>",
                bgcolor=bgcolor,
                shape="box",
                style="filled",
            )
            # generate edges for connector loops
            if len(connector.loops) > 0:
                dot.attr("edge", color="#000000")
                loops = gv_connector_loops(connector)
                for head, tail in loops:
                    dot.edge(head, tail)

        # determine if there are double- or triple-colored wires in the harness;
        # if so, pad single-color wires to make all wires of equal thickness
        wire_is_multicolor = [
            len(wire.color) > 1
            for cable in self.cables.values()
            for wire in cable.wire_objects
        ]
        if any(wire_is_multicolor):
            wireviz.wv_colors.padding_amount = 3
        else:
            wireviz.wv_colors.padding_amount = 1

        for cable in self.cables.values():
            # generate cable node
            # TODO: PN info for bundles (per wire)
            gv_html = gv_node_component(cable)
            bgcolor = calculate_node_bgcolor(cable, self.options)
            style = "filled,dashed" if cable.category == "bundle" else "filled"
            dot.node(
                cable.designator,
                label=f"<\n{gv_html}\n>",
                bgcolor=bgcolor,
                shape="box",
                style=style,
            )

            # generate wire edges between component nodes and cable nodes
            for connection in cable._connections:
                color, l1, l2, r1, r2 = gv_edge_wire(self, cable, connection)
                dot.attr("edge", color=color)
                if not (l1, l2) == (None, None):
                    dot.edge(l1, l2)
                if not (r1, r2) == (None, None):
                    dot.edge(r1, r2)

        for mate in self.mates:
            color, dir, code_from, code_to = gv_edge_mate(mate)

            dot.attr("edge", color=color, style="dashed", dir=dir)
            dot.edge(code_from, code_to)

        apply_dot_tweaks(dot, self.tweak)

        return dot

    # cache for the GraphViz Graph object
    # do not access directly, use self.graph instead
    _graph = None

    @property
    def graph(self):
        if not self._graph:  # no cached graph exists, generate one
            self._graph = self.create_graph()
        return self._graph  # return cached graph

    @property
    def png(self):
        from io import BytesIO

        graph = self.graph
        data = BytesIO()
        data.write(graph.pipe(format="png"))
        data.seek(0)
        return data.read()

    @property
    def svg(self):
        graph = self.graph
        return embed_svg_images(graph.pipe(format="svg").decode("utf-8"), Path.cwd())

    def output(
        self,
        filename: (str, Path),
        view: bool = False,
        cleanup: bool = True,
        fmt: tuple = ("html", "png", "svg", "tsv"),
    ) -> None:
        # graphical output
        graph = self.graph
        for f in fmt:
            if f in ("png", "svg", "html"):
                if f == "html":  # if HTML format is specified,
                    f = "svg"  # generate SVG for embedding into HTML
                # SVG file will be renamed/deleted later
                _filename = f"{filename}.tmp" if f == "svg" else filename
                # TODO: prevent rendering SVG twice when both SVG and HTML are specified
                graph.format = f
                graph.render(filename=_filename, view=view, cleanup=cleanup)
        # embed images into SVG output
        if "svg" in fmt or "html" in fmt:
            embed_svg_images_file(f"{filename}.tmp.svg")
        # GraphViz output
        if "gv" in fmt:
            graph.save(filename=f"{filename}.gv")
        # BOM output
        # bomlist = bom_list(self.bom())
        bomlist = [[]]
        if "tsv" in fmt:
            open_file_write(f"{filename}.bom.tsv").write(tuplelist2tsv(bomlist))
        if "csv" in fmt:
            # TODO: implement CSV output (preferrably using CSV library)
            print("CSV output is not yet supported")
        # HTML output
        if "html" in fmt:
            generate_html_output(filename, bomlist, self.metadata, self.options)
        # PDF output
        if "pdf" in fmt:
            # TODO: implement PDF output
            print("PDF output is not yet supported")
        # delete SVG if not needed
        if "html" in fmt and not "svg" in fmt:
            # SVG file was just needed to generate HTML
            Path(f"{filename}.tmp.svg").unlink()
        elif "svg" in fmt:
            Path(f"{filename}.tmp.svg").replace(f"{filename}.svg")

    # def bom(self):
    #     if not self._bom:
    #         self._bom = generate_bom(self)
    #     return self._bom
