import unittest

from pydantic import ValidationError

from worker.models import ExperimentRequest


def valid_payload():
    return {
        "dataset_id": "openbb-demo",
        "universe": "demo",
        "train": {"start": "2008-01-01", "end": "2014-12-31"},
        "valid": {"start": "2015-01-01", "end": "2016-12-31"},
        "test": {"start": "2017-01-01", "end": "2020-08-01"},
    }


class ExperimentRequestTests(unittest.TestCase):
    def test_requires_dataset_and_universe(self):
        payload = valid_payload()
        payload.pop("dataset_id")
        with self.assertRaises(ValidationError):
            ExperimentRequest.model_validate(payload)

        payload = valid_payload()
        payload.pop("universe")
        with self.assertRaises(ValidationError):
            ExperimentRequest.model_validate(payload)

    def test_accepts_allowlisted_experiment_shape(self):
        request = ExperimentRequest.model_validate(valid_payload())
        self.assertEqual(request.dataset_id, "openbb-demo")
        self.assertEqual(request.universe, "demo")
        self.assertEqual(request.feature_set, "Alpha158")
        self.assertEqual(request.model, "lightgbm")
        self.assertEqual(request.strategy.topk, 50)

    def test_accepts_allowlisted_models(self):
        for model in ("linear", "xgboost", "catboost"):
            with self.subTest(model=model):
                payload = valid_payload()
                payload["model"] = model
                request = ExperimentRequest.model_validate(payload)
                self.assertEqual(request.model, model)

    def test_accepts_custom_dataset_and_universe(self):
        payload = valid_payload()
        payload["dataset_id"] = "openbb-sp500-demo"
        payload["universe"] = "sp500_demo"
        request = ExperimentRequest.model_validate(payload)
        self.assertEqual(request.dataset_id, "openbb-sp500-demo")
        self.assertEqual(request.universe, "sp500_demo")

    def test_rejects_invalid_dataset_id(self):
        payload = valid_payload()
        payload["dataset_id"] = "../bad"
        with self.assertRaises(ValidationError):
            ExperimentRequest.model_validate(payload)

    def test_rejects_overlapping_segments(self):
        payload = valid_payload()
        payload["valid"]["start"] = "2014-12-31"
        with self.assertRaises(ValidationError):
            ExperimentRequest.model_validate(payload)

    def test_rejects_unknown_model(self):
        payload = valid_payload()
        payload["model"] = "transformer"
        with self.assertRaises(ValidationError):
            ExperimentRequest.model_validate(payload)

    def test_rejects_drop_larger_than_topk(self):
        payload = valid_payload()
        payload["strategy"] = {"topk": 5, "n_drop": 6}
        with self.assertRaises(ValidationError):
            ExperimentRequest.model_validate(payload)
