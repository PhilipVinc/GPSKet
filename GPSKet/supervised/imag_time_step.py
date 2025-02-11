import netket as nk
import jax
import jax.numpy as jnp
import copy

from netket.vqs.mc.mc_state.expect_chunked import get_local_kernel
from netket.vqs.mc import get_local_kernel_arguments, get_local_kernel

from netket.utils import wrap_afun

from flax.core import freeze

from functools import partial

class ImagTimeStep():
    def __init__(self, vstate, hamiltonian):
        self.vstate = vstate
        self.hamiltonian = hamiltonian

    def get_local_energies(self, samples):
        # This is a little bit hacky, the interface should probably be improved at one point
        old_samples = self.vstate._samples
        self.vstate._samples = samples
        loc_ens = self.vstate.local_estimators(self.hamiltonian, chunk_size=self.vstate.chunk_size)
        self.vstate._samples = old_samples
        return loc_ens

    def log_imag_time_step(self, tau, samples):
        samples_reshaped = samples.reshape((-1, samples.shape[-1]))
        self.log_amps = self.vstate.log_value(samples_reshaped)
        self.local_energies = self.get_local_energies(samples_reshaped)
        return self.log_amps + jnp.log(1 - tau * self.local_energies)

def get_imag_time_step_vstate(tau, hamiltonian, vstate):
    """Returns a variational state with a first order imaginary time evolved model (i.e. (1 - tau H)|Psi>)
    based on a given variational state, can therefore be nested arbitrarily often.
    Fast updating (if requested) is currently only applied in the innermost local energy
    evaluations (which could be slightly improved in the future...).
    CAREFUL: This is only correct for custom Hamiltonians evaluating the energy on the fly (without explicitly
    generating the connected configurations).
    TODO: Add check that evaluation is sensible with the given Hamiltonian (not entirely trivial)

    Args:
        tau: Propagation time
        hamiltonian: The Hamiltonian for the imaginary time evolution
        vstate: The original variational state

    Returns:
        The variational state with updated model (based on a single imaginary time step)
    """
    log_model = vstate._apply_fun
    _, args = get_local_kernel_arguments(vstate, hamiltonian)
    if vstate.chunk_size is None:
        local_estimator_fun = get_local_kernel(vstate, hamiltonian)
    else:
        local_estimator_fun = get_local_kernel(vstate, hamiltonian, vstate.chunk_size)
    def imag_time_model_log_amp(model_pars, samples):
        pars, tau = freeze(model_pars).pop("tau")
        samps = samples.reshape((-1, samples.shape[-1]))
        loc_ens = local_estimator_fun(log_model, pars, samps, args)
        log_amps = log_model(pars, samps)
        return log_amps + jnp.log(1/tau - 1 * loc_ens) + jnp.log(tau)
    new_vstate = copy.deepcopy(vstate)
    new_vstate._apply_fun = imag_time_model_log_amp
    new_vstate._model = wrap_afun(imag_time_model_log_amp)
    new_vstate.model_state = {"tau": tau, **new_vstate.model_state}
    return new_vstate