import copy
import json
import os
import pprint

import bluesky.plans as bp
import bluesky.plan_stubs as bps
import dictdiffer
import matplotlib.pyplot as plt
import numpy as np
import peakutils
import pytest
import tfs

from sirepo_bluesky.madx_flyer import MADXFlyer
from sirepo_bluesky.sirepo_ophyd import BeamStatisticsReport, create_classes


def test_beamline_elements_as_ophyd_objects(srw_tes_simulation):
    classes, objects = create_classes(
        srw_tes_simulation.data, connection=srw_tes_simulation
    )

    for name, obj in objects.items():
        pprint.pprint(obj.read())

    globals().update(**objects)

    print(mono_crystal1.summary())  # noqa
    pprint.pprint(mono_crystal1.read())  # noqa


@pytest.mark.parametrize("method", ["set", "put"])
def test_beamline_elements_set_put(srw_tes_simulation, method):
    classes, objects = create_classes(
        srw_tes_simulation.data, connection=srw_tes_simulation
    )
    globals().update(**objects)

    for i, (k, v) in enumerate(objects.items()):
        if "element_position" in v.component_names:
            old_value = v.element_position.get()
            old_sirepo_value = srw_tes_simulation.data["models"]["beamline"][i][
                "position"
            ]

            getattr(v.element_position, method)(old_value + 100)

            new_value = v.element_position.get()
            new_sirepo_value = srw_tes_simulation.data["models"]["beamline"][i][
                "position"
            ]

            print(
                f"\n  Changed: {old_value} -> {new_value}\n   Sirepo: {old_sirepo_value} -> {new_sirepo_value}\n"
            )

            assert old_value == old_sirepo_value
            assert new_value == new_sirepo_value
            assert new_value != old_value
            assert abs(new_value - (old_value + 100)) < 1e-8


@pytest.mark.parametrize("method", ["set", "put"])
def test_grazing_angle_calculation(srw_tes_simulation, method):
    classes, objects = create_classes(
        srw_tes_simulation.data, connection=srw_tes_simulation
    )
    globals().update(**objects)

    params_before = copy.deepcopy(toroid.grazingAngle._sirepo_dict)  # noqa F821
    params_before.pop("grazingAngle")

    getattr(toroid.grazingAngle, method)(10)  # noqa F821

    params_after = copy.deepcopy(toroid.grazingAngle._sirepo_dict)  # noqa F821
    params_after.pop("grazingAngle")

    params_diff = list(dictdiffer.diff(params_before, params_after))
    assert len(params_diff) > 0  # should not be empty

    expected_vector_values = {
        "nvx": 0,
        "nvy": 0.9999500004166653,
        "nvz": -0.009999833334166664,
        "tvx": 0,
        "tvy": 0.009999833334166664,
    }

    actual_vector_values = {
        "nvx": toroid.normalVectorX.get(),  # noqa F821
        "nvy": toroid.normalVectorY.get(),  # noqa F821
        "nvz": toroid.normalVectorZ.get(),  # noqa F821
        "tvx": toroid.tangentialVectorX.get(),  # noqa F821
        "tvy": toroid.tangentialVectorY.get(),  # noqa F821
    }

    assert not list(dictdiffer.diff(expected_vector_values, actual_vector_values))


def test_beamline_elements_simple_connection(srw_basic_simulation):
    classes, objects = create_classes(
        srw_basic_simulation.data, connection=srw_basic_simulation
    )

    for name, obj in objects.items():
        pprint.pprint(obj.read())

    globals().update(**objects)

    print(watchpoint.summary())  # noqa F821
    pprint.pprint(watchpoint.read())  # noqa F821


def test_srw_source_with_run_engine(RE, db, srw_ari_simulation, num_steps=5):
    classes, objects = create_classes(
        srw_ari_simulation.data,
        connection=srw_ari_simulation,
        extra_model_fields=["undulator", "intensityReport"],
    )
    globals().update(**objects)

    undulator.verticalAmplitude.kind = "hinted"  # noqa F821

    single_electron_spectrum.initialEnergy.get()  # noqa F821
    single_electron_spectrum.initialEnergy.put(20)  # noqa F821
    single_electron_spectrum.finalEnergy.put(1100)  # noqa F821

    assert srw_ari_simulation.data["models"]["intensityReport"]["initialEnergy"] == 20
    assert srw_ari_simulation.data["models"]["intensityReport"]["finalEnergy"] == 1100

    (uid,) = RE(
        bp.scan(
            [single_electron_spectrum],  # noqa F821
            undulator.verticalAmplitude,  # noqa F821
            0.2,
            1,
            num_steps,
        )
    )  # noqa F821

    hdr = db[uid]
    tbl = hdr.table()
    print(tbl)

    ses_data = np.array(list(hdr.data("single_electron_spectrum_image")))
    ampl_data = np.array(list(hdr.data("undulator_verticalAmplitude")))
    # Check the shape of the image data is right:
    assert ses_data.shape == (num_steps, 2000)

    resource_files = []
    for name, doc in hdr.documents():
        if name == "resource":
            resource_files.append(os.path.basename(doc["resource_path"]))

    # Check that all resource files are unique:
    assert len(set(resource_files)) == num_steps

    fig = plt.figure()
    ax = fig.add_subplot()
    for i in range(num_steps):
        ax.plot(ses_data[i, :], label=f"vert. magn. fld. {ampl_data[i]:.3f}T")
        peak = peakutils.indexes(ses_data[i, :])
        ax.scatter(peak, ses_data[i, peak])
    ax.grid()
    ax.legend()
    ax.set_title("Single-Electron Spectrum vs. Vertical Magnetic Field")
    fig.savefig("ses-vs-ampl.png")
    # plt.show()


def test_shadow_with_run_engine(RE, db, shadow_tes_simulation, num_steps=5):
    classes, objects = create_classes(
        shadow_tes_simulation.data, connection=shadow_tes_simulation
    )
    globals().update(**objects)

    aperture.horizontalSize.kind = "hinted"  # noqa F821

    (uid,) = RE(bp.scan([w9], aperture.horizontalSize, 0, 2, num_steps))  # noqa F821
    hdr = db[uid]
    tbl = hdr.table(fill=True)
    print(tbl)

    # Check that the duration for each step in the simulation is positive:
    sim_durations = np.array(tbl["w9_duration"])
    assert (sim_durations > 0.0).all()

    w9_image = np.array(list(hdr.data("w9_image")))
    # Check the shape of the image data is right:
    assert w9_image.shape == (num_steps, 100, 100)

    w9_mean_from_image = w9_image.mean(axis=(1, 2))
    w9_mean_from_table = np.array(tbl["w9_mean"])

    # Check the number of elements correspond to a number of scan points:
    assert len(w9_mean_from_table) == num_steps

    # Check that an average values of the first and last images are right:
    assert np.allclose(w9_image[0].mean(), 0.0)
    assert np.allclose(w9_image[-1].mean(), 0.255665516042795)

    # Check that the values from the table and averages from the image data are
    # the same:
    assert np.allclose(w9_mean_from_table, w9_mean_from_image)

    # Check that the averaged intensities from the table are ascending:
    assert np.all(np.diff(w9_mean_from_table) > 0)

    resource_files = []
    for name, doc in hdr.documents():
        if name == "resource":
            resource_files.append(os.path.basename(doc["resource_path"]))

    # Check that all resource files are unique:
    assert len(set(resource_files)) == num_steps


def test_beam_statistics_report_only(RE, db, shadow_tes_simulation):
    classes, objects = create_classes(
        shadow_tes_simulation.data, connection=shadow_tes_simulation
    )
    globals().update(**objects)

    bsr = BeamStatisticsReport(name="bsr", connection=shadow_tes_simulation)

    toroid.r_maj.kind = "hinted"  # noqa F821

    scan_range = (10_000, 50_000, 21)

    (uid,) = RE(bp.scan([bsr], toroid.r_maj, *scan_range))  # noqa F821
    hdr = db[uid]
    tbl = hdr.table()
    print(tbl)

    calc_durations = np.array(tbl["time"].diff(), dtype=float)[1:] / 1e9
    print(f"Calculated durations (seconds): {calc_durations}")

    # Check that the duration for each step in the simulation is non-zero:
    cpt_durations = np.array(tbl["bsr_duration"])
    print(f"Durations from component (seconds): {cpt_durations}")

    assert (cpt_durations > 0.0).all()
    assert (calc_durations > cpt_durations[1:]).all()

    fig = plt.figure()
    ax = fig.add_subplot()
    ax.plot(np.linspace(*scan_range)[1:], calc_durations)
    ax.set_ylabel("Duration of simulations [s]")
    ax.set_xlabel("Torus Major Radius [m]")
    title = (
        f"Shadow TES simulation\n"
        f"RE(bp.scan([bsr], toroid.r_maj, "
        f"{', '.join([str(x) for x in scan_range])}))"
    )
    ax.set_title(title)
    fig.savefig("TES-Shadow-timing.png")
    # plt.show()


def test_beam_statistics_report_and_watchpoint(RE, db, shadow_tes_simulation):
    classes, objects = create_classes(
        shadow_tes_simulation.data, connection=shadow_tes_simulation
    )
    globals().update(**objects)

    bsr = BeamStatisticsReport(name="bsr", connection=shadow_tes_simulation)

    toroid.r_maj.kind = "hinted"  # noqa F821

    (uid,) = RE(bp.scan([bsr, w9], toroid.r_maj, 10000, 50000, 5))  # noqa F821
    hdr = db[uid]
    tbl = hdr.table()
    print(tbl)

    w9_data_1 = json.loads(tbl["w9_sirepo_data_json"][1])
    w9_data_5 = json.loads(tbl["w9_sirepo_data_json"][5])

    bsr_data_1 = json.loads(tbl["bsr_sirepo_data_json"][1])
    bsr_data_5 = json.loads(tbl["bsr_sirepo_data_json"][5])

    w9_diffs = list(dictdiffer.diff(w9_data_1, w9_data_5))
    assert w9_diffs == [
        ("change", ["models", "beamline", 5, "r_maj"], (10000.0, 50000.0))
    ]

    bsr_diffs = list(dictdiffer.diff(bsr_data_1, bsr_data_5))
    assert bsr_diffs == [
        ("change", ["models", "beamline", 5, "r_maj"], (10000.0, 50000.0))
    ]

    w9_bsr_diffs = list(dictdiffer.diff(w9_data_1, bsr_data_5))
    assert w9_bsr_diffs == [
        ("change", ["models", "beamline", 5, "r_maj"], (10000.0, 50000.0)),
        ("change", "report", ("watchpointReport12", "beamStatisticsReport")),
    ]


@pytest.mark.parametrize("method", ["set", "put"])
def test_mad_x_elements_set_put(madx_resr_storage_ring_simulation, method):
    connection = madx_resr_storage_ring_simulation
    data = connection.data
    classes, objects = create_classes(data, connection=connection)
    globals().update(**objects)

    for i, (k, v) in enumerate(objects.items()):
        old_value = v.l.get()  # l is length
        old_sirepo_value = data["models"]["elements"][i]["l"]

        getattr(v.l, method)(old_value + 10)

        new_value = v.l.get()
        new_sirepo_value = data["models"]["elements"][i]["l"]

        print(
            f"\n  Changed: {old_value} -> {new_value}\n   Sirepo: {old_sirepo_value} -> {new_sirepo_value}\n"
        )

        assert old_value == old_sirepo_value
        assert new_value == new_sirepo_value
        assert new_value != old_value
        assert abs(new_value - (old_value + 10)) < 1e-8


def test_mad_x_elements_simple_connection(madx_bl2_triplet_tdc_simulation):
    connection = madx_bl2_triplet_tdc_simulation
    data = connection.data
    classes, objects = create_classes(data, connection=connection)
    for name, obj in objects.items():
        pprint.pprint(obj.read())

    globals().update(**objects)

    print(bpm5.summary())  # noqa
    pprint.pprint(bpm5.read())  # noqa


def test_madx_with_run_engine(RE, db, madx_bl2_triplet_tdc_simulation):
    connection = madx_bl2_triplet_tdc_simulation
    data = connection.data
    classes, objects = create_classes(data, connection=connection)
    globals().update(**objects)

    madx_flyer = MADXFlyer(
        connection=connection,
        root_dir="/tmp/sirepo-bluesky-data",
        report="elementAnimation250-20",
    )

    (uid,) = RE(bp.fly([madx_flyer]))  # noqa F821
    hdr = db[uid]
    tbl = hdr.table(stream_name="madx_flyer", fill=True)
    print(tbl)

    resource_files = []
    for name, doc in hdr.documents():
        if name == "resource":
            resource_files.append(os.path.join(doc["root"], doc["resource_path"]))

    # Check that we have only one resource madx file for all datum documents:
    assert len(set(resource_files)) == 1

    df = tfs.read(resource_files[0])
    for column in df.columns:
        if column == "NAME":
            assert (
                tbl[f"madx_flyer_{column}"].astype("string").values == df[column].values
            ).all()
        else:
            assert np.allclose(
                np.array(tbl[f"madx_flyer_{column}"]).astype(float),
                np.array(df[column]),
            )


def test_madx_variables_with_run_engine(RE, db, madx_bl2_triplet_tdc_simulation):
    connection = madx_bl2_triplet_tdc_simulation
    data = connection.data
    classes, objects = create_classes(
        data,
        connection=connection,
        extra_model_fields=["rpnVariables"],
    )

    globals().update(**objects)

    assert len(objects) == len(data["models"]["elements"]) + len(
        data["models"]["rpnVariables"]
    )

    madx_flyer = MADXFlyer(
        connection=connection,
        root_dir="/tmp/sirepo-bluesky-data",
        report="elementAnimation250-20",
    )

    def madx_plan(parameter=ihq1, value=2.0):  # noqa F821
        yield from bps.mv(parameter.value, value)
        return (yield from bp.fly([madx_flyer]))

    (uid,) = RE(madx_plan())  # noqa F821
    hdr = db[uid]
    tbl = hdr.table(stream_name="madx_flyer", fill=True)
    print(tbl)

    S = [
        0.2,
        1.34,
        4.76,
        5.9,
        7.4,
        8.54,
        9.6105,
        12.38425,
        16.69165,
        18.94165,
        20.06665,
        21.13165,
        21.49665,
        22.49665,
        22.49665,
        23.34165,
        24.18165,
        24.31165,
        24.56565,
        24.99065,
        26.02065,
        28.86265,
    ]
    BETX = [
        10.408,
        22.36308646,
        54.54905034,
        68.56397971,
        89.50807112,
        307.4856416,
        40.73865381,
        294.9778573,
        9.096680097,
        107.8726081,
        319.2483447,
        199.0675119,
        91.07793387,
        8.802320195,
        8.802320195,
        86.43782501,
        103.4247185,
        106.1910538,
        111.7023327,
        579.491275,
        5793.638223,
        3.239075108,
    ]
    BETY = [
        10.408,
        3.896222946,
        22.97357858,
        49.11590862,
        98.58402476,
        5.817318976,
        23.09293647,
        144.6412107,
        77.24882134,
        28.57325457,
        0.185399505,
        28.69475679,
        56.73496998,
        182.0833344,
        182.0833344,
        225.3607656,
        36.86708925,
        21.98084814,
        3.944913321,
        8.171674062,
        224.1750557,
        0.1061422279,
    ]

    assert np.allclose(np.array(tbl["madx_flyer_S"]).astype(float), S)
    assert np.allclose(np.array(tbl["madx_flyer_BETX"]).astype(float), BETX)
    assert np.allclose(np.array(tbl["madx_flyer_BETY"]).astype(float), BETY)


def test_madx_commands_with_run_engine(RE, db, madx_bl2_triplet_tdc_simulation):
    connection = madx_bl2_triplet_tdc_simulation
    data = connection.data
    classes, objects = create_classes(
        data,
        connection=connection,
        extra_model_fields=["commands"],
    )

    globals().update(**objects)

    assert len(objects) == len(data["models"]["elements"]) + len(
        data["models"]["commands"]
    )

    madx_flyer = MADXFlyer(
        connection=connection,
        root_dir="/tmp/sirepo-bluesky-data",
        report="elementAnimation250-20",
    )

    def madx_plan(element=match16, value=1.0):  # noqa F821
        yield from bps.mv(element.deltap, value)
        return (yield from bp.fly([madx_flyer]))

    (uid,) = RE(madx_plan())  # noqa F821
    hdr = db[uid]
    tbl = hdr.table(stream_name="madx_flyer", fill=True)
    print(tbl)

    S = [
        0.20000,
        1.34000,
        4.76000,
        5.90000,
        7.40000,
        8.54000,
        9.61050,
        12.38425,
        16.69165,
        18.94165,
        20.06665,
        21.13165,
        21.49665,
        22.49665,
        22.49665,
        23.34165,
        24.18165,
        24.31165,
        24.56565,
        24.99065,
        26.02065,
        28.86265,
    ]
    BETX = [
        10.408000,
        10.354387,
        3.814022,
        3.244625,
        3.722423,
        13.560196,
        2.071744,
        12.418481,
        2.522746,
        4.848420,
        10.179698,
        5.237850,
        2.047153,
        1.263596,
        1.263596,
        2.600907,
        1.912882,
        1.874522,
        1.852264,
        4.701896,
        28.344746,
        0.272005,
    ]
    BETY = [
        10.408000,
        9.453506,
        2.957846,
        2.723835,
        3.887070,
        0.432363,
        2.708033,
        6.227945,
        179.081303,
        780.653538,
        502.913016,
        292.055947,
        303.779822,
        337.086493,
        337.086493,
        273.558544,
        1.608260,
        1.205618,
        32.661415,
        78.122592,
        92.869304,
        12.453373,
    ]

    assert np.allclose(np.array(tbl["madx_flyer_S"]).astype(float), S)
    assert np.allclose(np.array(tbl["madx_flyer_BETX"]).astype(float), BETX)
    assert np.allclose(np.array(tbl["madx_flyer_BETY"]).astype(float), BETY)


def test_madx_variables_and_commands_with_run_engine(
    RE, db, madx_bl2_triplet_tdc_simulation
):
    connection = madx_bl2_triplet_tdc_simulation
    data = connection.data
    classes, objects = create_classes(
        data,
        connection=connection,
        extra_model_fields=["rpnVariables", "commands"],
    )

    globals().update(**objects)

    assert len(objects) == len(data["models"]["elements"]) + len(
        data["models"]["rpnVariables"]
    ) + len(data["models"]["commands"])

    madx_flyer = MADXFlyer(
        connection=connection,
        root_dir="/tmp/sirepo-bluesky-data",
        report="elementAnimation250-20",
    )

    def madx_plan(element=match16, parameter=ihq1, value=1.0):  # noqa F821
        yield from bps.mv(element.deltap, value)
        yield from bps.mv(parameter.value, value)
        return (yield from bp.fly([madx_flyer]))

    (uid,) = RE(madx_plan())  # noqa F821
    hdr = db[uid]
    tbl = hdr.table(stream_name="madx_flyer", fill=True)
    print(tbl)

    S = [
        0.2,
        1.34,
        4.76,
        5.9,
        7.4,
        8.54,
        9.6105,
        12.38425,
        16.69165,
        18.94165,
        20.06665,
        21.13165,
        21.49665,
        22.49665,
        22.49665,
        23.34165,
        24.18165,
        24.31165,
        24.56565,
        24.99065,
        26.02065,
        28.86265,
    ]
    BETX = [
        10.408,
        26.36823148,
        79.46190212,
        103.6210282,
        140.3313459,
        488.789931,
        65.86466965,
        454.5456102,
        50.72568002,
        179.8669197,
        354.5215834,
        169.0406808,
        59.35075109,
        46.39089107,
        46.39089107,
        88.90826335,
        42.32938156,
        36.64972828,
        26.73519957,
        86.25259069,
        756.5705174,
        0.109784421,
    ]
    BETY = [
        10.408,
        2.838597398,
        39.69699101,
        79.82049262,
        153.8203268,
        8.369019056,
        39.62777489,
        229.4736303,
        7035.006014,
        30696.98669,
        19782.62676,
        11495.79344,
        11959.99285,
        13278.8394,
        13278.8394,
        10779.68184,
        63.71732299,
        46.92980128,
        1283.904078,
        642.7747039,
        7457.247957,
        302.100827,
    ]

    assert np.allclose(np.array(tbl["madx_flyer_S"]).astype(float), S)
    assert np.allclose(np.array(tbl["madx_flyer_BETX"]).astype(float), BETX)
    assert np.allclose(np.array(tbl["madx_flyer_BETY"]).astype(float), BETY)
