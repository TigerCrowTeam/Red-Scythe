"""Group the packet protocol components used by the radio application.

framing defines wire packets; authentication loads identity keys;
replay_protection tracks accepted sessions and sequences; reliability manages
packet queues, acknowledgements, retries and duplicate suppression.
"""
