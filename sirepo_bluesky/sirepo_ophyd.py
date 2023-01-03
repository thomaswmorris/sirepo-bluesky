import copy
import datetime
import hashlib
import json
import logging
import time
from collections import deque, namedtuple
from pathlib import Path

import numpy as np

import inflection
from event_model import compose_resource
from ophyd import Component as Cpt
from ophyd import Device, Signal
from ophyd.sim import NullStatus, new_uid

from . import ExternalFileReference
from .srw_handler import read_srw_file
from .shadow_handler import read_shadow_file

logger = logging.getLogger("sirepo-bluesky")
# Note: the following handler could be created/added to the logger on the client side:
# import sys
# stream_handler = logging.StreamHandler(sys.stdout)
# logger.addHandler(stream_handler)

RESERVED_OPHYD_TO_SIREPO_ATTRS = {  # ophyd <-> sirepo
    "position": "element_position",
    "name": "element_name",
}
RESERVED_SIREPO_TO_OPHYD_ATTRS = {
    v: k for k, v in RESERVED_OPHYD_TO_SIREPO_ATTRS.items()
}


class SirepoSignal(Signal):
    def __init__(self, sirepo_dict, sirepo_param, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sirepo_dict = sirepo_dict
        self._sirepo_param = sirepo_param
        if sirepo_param in RESERVED_SIREPO_TO_OPHYD_ATTRS:
            self._sirepo_param = RESERVED_SIREPO_TO_OPHYD_ATTRS[sirepo_param]

    def set(self, value, *, timeout=None, settle_time=None):
        logger.debug(f"Setting value for {self.name} to {value}")
        self._sirepo_dict[self._sirepo_param] = value
        self._readback = value
        return NullStatus()

    def put(self, *args, **kwargs):
        self.set(*args, **kwargs).wait()


class DeviceWithJSONData(Device):
    sirepo_data_json = Cpt(Signal, kind="normal", value="")
    sirepo_data_hash = Cpt(Signal, kind="normal", value="")
    duration = Cpt(Signal, kind="normal", value=-1.0)

    def trigger(self, *args, **kwargs):
        super().trigger(*args, **kwargs)

        json_str = json.dumps(self.connection.data)
        json_hash = hashlib.sha256(json_str.encode()).hexdigest()
        self.sirepo_data_json.put(json_str)
        self.sirepo_data_hash.put(json_hash)

        return NullStatus()


class SirepoWatchpoint(DeviceWithJSONData):

    cx = Cpt(Signal, kind="hinted")
    cy = Cpt(Signal, kind="hinted")
    sx = Cpt(Signal, kind="hinted")
    sy = Cpt(Signal, kind="hinted")
    density = Cpt(Signal, kind="hinted")

    image = Cpt(ExternalFileReference, kind="normal")
    shape = Cpt(Signal)
    mean = Cpt(Signal, kind="hinted")
    photon_energy = Cpt(Signal, kind="normal")
    horizontal_extent = Cpt(Signal)
    vertical_extent = Cpt(Signal)

    def __init__(
        self,
        *args,
        root_dir="/tmp/sirepo-bluesky-data",
        assets_dir=None,
        result_file=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self._root_dir = root_dir
        self._assets_dir = assets_dir
        self._result_file = result_file

        self._asset_docs_cache = deque()
        self._resource_document = None
        self._datum_factory = None

        sim_type = self.connection.data["simulationType"]
        allowed_sim_types = ("srw", "shadow", "madx")
        if sim_type not in allowed_sim_types:
            raise RuntimeError(
                f"Unknown simulation type: {sim_type}\n"
                f"Allowed simulation types: {allowed_sim_types}"
            )


    def get_beam_stats(self, im, extents, thresh=np.exp(-2)):

        if not im.sum() > 0: return np.nan, np.nan, np.nan, np.nan, np.nan

        W = im.copy()
        W[W < thresh * W.max()] = 0

        nx, ny = im.shape
        X, Y = np.meshgrid(np.linspace(*extents[0],nx), np.linspace(*extents[1],ny), indexing='ij')

        cx = np.sum(W * X) / np.sum(W)
        cy = np.sum(W * Y) / np.sum(W)
        
        sx = np.sqrt(np.sum(W * np.square(X-cx)) / np.sum(W))
        sy = np.sqrt(np.sum(W * np.square(Y-cy)) / np.sum(W))

        return cx, cy, sx, sy, im.sum()

    def get_beam_stats(self, im, extents, q_beam=0.9):

        q  = np.linspace(0,1,256)
        dq = np.gradient(q).mean()
        nq = int(q_beam / dq)

        nx, ny = im.shape
        he, ve = extents 

        ncs1 = np.interp(q, np.cumsum(im.sum(axis=0))/im.sum(), np.arange(ny))
        ncs0 = np.interp(q, np.cumsum(im.sum(axis=1))/im.sum(), np.arange(nx))

        is0 = (ncs0[nq:] - ncs0[:-nq]).argmin()
        is1 = (ncs1[nq:] - ncs1[:-nq]).argmin()

        xb = np.interp([ncs0[is0], ncs0[is0+nq]], np.arange(nx), np.linspace(*he, nx))
        yb = np.interp([ncs1[is1], ncs1[is1+nq]], np.arange(ny), np.linspace(*ve, ny))

        return np.mean(xb), np.mean(yb), np.diff(xb)[0], np.diff(yb)[0], im.sum()

    def trigger(self, *args, **kwargs):
        logger.debug(f"Custom trigger for {self.name}")

        date = datetime.datetime.now()
        self._assets_dir = date.strftime("%Y/%m/%d")
        self._result_file = f"{new_uid()}.dat"

        self._resource_document, self._datum_factory, _ = compose_resource(
            start={"uid": "needed for compose_resource() but will be discarded"},
            spec=self.connection.data["simulationType"],
            root=self._root_dir,
            resource_path=str(Path(self._assets_dir) / Path(self._result_file)),
            resource_kwargs={},
        )
        # now discard the start uid, a real one will be added later
        self._resource_document.pop("run_start")
        self._asset_docs_cache.append(("resource", self._resource_document))

        sim_result_file = str(
            Path(self._resource_document["root"])
            / Path(self._resource_document["resource_path"])
        )

        self.connection.data["report"] = f"watchpointReport{self.id._sirepo_dict['id']}"

        _, duration = self.connection.run_simulation()
        self.duration.put(duration)

        datafile = self.connection.get_datafile(file_index=-1)

        with open(sim_result_file, "wb") as f:
            f.write(datafile)

        conn_data = self.connection.data
        sim_type = conn_data["simulationType"]
        if sim_type == "srw":
            ndim = 2  # this will always be a report with 2D data.
            ret = read_srw_file(sim_result_file, ndim=ndim)
            self._resource_document["resource_kwargs"]["ndim"] = ndim
        elif sim_type == "shadow":
            nbins = conn_data["models"][conn_data["report"]]["histogramBins"]
            ret = read_shadow_file(sim_result_file, histogram_bins=nbins)
            self._resource_document["resource_kwargs"]["histogram_bins"] = nbins

        def update_components(_data):

            cx, cy, sx, sy, pixsum = self.get_beam_stats(_data['data'][::-1], 
                                           (1e4*np.array(_data["horizontal_extent"]),
                                            1e4*np.array(_data["vertical_extent"])))
            self.cx.put(cx)
            self.cy.put(cy)
            self.sx.put(sx)
            self.sy.put(sy)
            self.density.put(pixsum/(sx*sy))

            self.shape.put(_data["shape"])
            self.mean.put(_data["mean"])
            self.photon_energy.put(_data["photon_energy"])
            self.horizontal_extent.put(_data["horizontal_extent"])
            self.vertical_extent.put(_data["vertical_extent"])

        update_components(ret)

        datum_document = self._datum_factory(datum_kwargs={})
        self._asset_docs_cache.append(("datum", datum_document))

        self.image.put(datum_document["datum_id"])

        self._resource_document = None
        self._datum_factory = None

        logger.debug(
            f"\nReport for {self.name}: {self.connection.data['report']}\n"
        )

        # We call the trigger on super at the end to update the sirepo_data_json
        # and the corresponding hash after the simulation is run.
        super().trigger(*args, **kwargs)
        return NullStatus()

    def describe(self):
        res = super().describe()
        res[self.image.name].update(dict(external="FILESTORE"))
        return res

    def unstage(self):
        super().unstage()
        self._resource_document = None

    def collect_asset_docs(self):
        items = list(self._asset_docs_cache)
        self._asset_docs_cache.clear()
        for item in items:
            yield item


class BeamStatisticsReport(DeviceWithJSONData):
    # NOTE: TES aperture changes don't seem to change the beam statistics
    # report graph on the website?

    report = Cpt(Signal, value={}, kind="normal")

    def __init__(self, connection, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connection = connection

    def trigger(self, *args, **kwargs):
        logger.debug(f"Custom trigger for {self.name}")

        self.connection.data["report"] = "beamStatisticsReport"

        start_time = time.monotonic()
        self.connection.run_simulation()
        self.duration.put(time.monotonic() - start_time)

        datafile = self.connection.get_datafile(file_index=-1)
        self.report.put(json.dumps(json.loads(datafile.decode())))

        logger.debug(
            f"\nReport for {self.name}: {self.connection.data['report']}\n"
        )

        # We call the trigger on super at the end to update the sirepo_data_json
        # and the corresponding hash after the simulation is run.
        super().trigger(*args, **kwargs)
        return NullStatus()

    def stage(self):
        super().stage()
        self.report.put({})

    def unstage(self):
        super().unstage()
        self.report.put({})


class SirepoSignalGrazingAngle(SirepoSignal):
    def set(self, value):
        super().set(value)
        ret = self.parent.connection.compute_grazing_orientation(self._sirepo_dict)
        # State is added to the ret dict from compute_grazing_orientation and we
        # want to make sure the vectors are updated properly every time the
        # grazing angle is updated.
        ret.pop("state")
        # Update vector components
        for cpt in [
            "normalVectorX",
            "normalVectorY",
            "normalVectorZ",
            "tangentialVectorX",
            "tangentialVectorY",
        ]:
            getattr(self.parent, cpt).put(ret[cpt])
        return NullStatus()


def create_classes(sirepo_data, connection, create_objects=True,
                   extra_model_fields=[]):
    classes = {}
    objects = {}
    data = copy.deepcopy(sirepo_data)

    sim_type = connection.sim_type

    SimTypeConfig = namedtuple("SimTypeConfig", "element_location class_name_field")

    srw_config = SimTypeConfig("beamline", "title")
    shadow_config = SimTypeConfig("beamline", "title")
    madx_config = SimTypeConfig("elements", "element_name")

    config_dict = {
        "srw": srw_config,
        "shadow": shadow_config,
        "madx": madx_config,
    }

    model_fields = [config_dict[sim_type].element_location] + extra_model_fields

    data_models = {}
    for model_field in model_fields:
        data_models[model_field] = data["models"][model_field]

    for model_field, data_model in data_models.items():
        for i, el in enumerate(data_model):
            logger.debug(f"Processing {el}...")

            for ophyd_key, sirepo_key in RESERVED_OPHYD_TO_SIREPO_ATTRS.items():
                # We have to rename the reserved attribute names. Example error
                # from ophyd:
                #
                #   TypeError: The attribute name(s) {'position'} are part of the
                #   bluesky interface and cannot be used as component names. Choose
                #   a different name.
                if ophyd_key in el:
                    el[sirepo_key] = el[ophyd_key]
                    el.pop(ophyd_key)
                else:
                    pass

            class_name = inflection.camelize(
                el[config_dict[sim_type].class_name_field]
                .replace(" ", "_")
                .replace(".", "")
            )
            object_name = inflection.underscore(class_name)

            base_classes = (Device,)
            extra_kwargs = {"connection": connection}
            if "type" in el and el["type"] == "watch":
                base_classes = (SirepoWatchpoint, Device)

            components = {}
            for k, v in el.items():

                if "type" in el and el["type"] in ["sphericalMirror", "toroidalMirror", "ellipsoidMirror"] \
                        and k == "grazingAngle":
                    cpt_class = SirepoSignalGrazingAngle
                else:
                    # TODO: Cover the cases for mirror and crystal grazing angles
                    cpt_class = SirepoSignal

                components[k] = Cpt(
                    cpt_class,
                    value=v,
                    sirepo_dict=sirepo_data["models"][model_field][i],
                    sirepo_param=k,
                )
            components.update(**extra_kwargs)

            cls = type(
                class_name,
                base_classes,
                components,
            )

            classes[object_name] = cls
            if create_objects:
                objects[object_name] = cls(name=object_name)

    return classes, objects
