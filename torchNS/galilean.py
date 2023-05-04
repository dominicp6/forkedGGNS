import torch
from torchNS.nested_sampling import NestedSampler
from torchNS.param import Param, NSPoints
from random import randint
from numpy import clip, pi

# Default floating point type
dtype = torch.float32

class GaliNest(NestedSampler):
    def __init__(self, loglike, params, nlive=50, tol=0.1, dt_ini=0.5, max_nsteps=1000000, clustering=False, verbose=True, score=None, device=None):
        super().__init__(loglike, params, nlive, tol, max_nsteps, clustering, verbose, device)

        self.acceptance_rate = 1.0
        self.n_tries = 0
        self.n_repeats = int(2 * self.nparams)

        self.dt = dt_ini
        self.n_in_steps = 0
        self.n_out_steps = 0

        self.given_score = score is not None
        if self.given_score:
            self.score = score

        self.n_tries_pure_ns = 0
        self.n_accepted_pure_ns = 0
        self.acc_rate_pure_ns = 1

        self._lower = torch.tensor([p.prior[0] for p in self.params], dtype=dtype, device=self.device)
        self._upper = torch.tensor([p.prior[1] for p in self.params], dtype=dtype, device=self.device)
        #assert p.prior_type == "uniform" for p in self.params, "Prior must be uniform for now"

    #@torch.compile
    def simulate_particle_in_box(self, position, velocity, min_like, dt, num_steps):
        """
        Simulate the motion of a particle in a box with walls defined by the function p(X) = p0,
        where X is a three-vector (x, y, z), using PyTorch.

        Args:
            position (torch.Tensor): Initial position of the particle, shape (3,).
            velocity (torch.Tensor): Initial velocity of the particle, shape (3,).
            p_func (callable): Function that computes p(X) for a given three-vector X.
            p0 (float): Value of p0 for the walls of the box.
            dt (float): Time step for numerical integration.
            num_steps (int): Number of time steps to simulate.

        Returns:
            position_history (torch.Tensor): History of particle positions, shape (num_steps+1, 3).
        """
        assert(len(position.shape) == 2), "Position must be a 2D tensor"
        for step in range(num_steps):
            # reflected = False
            position += velocity * dt
            # Slightly perturb the position to decorrelate the samples
            # position *= (1 + 1e-2 * torch.randn_like(position))
            p_x, grad_p_x = self.get_score(position)

            reflected = p_x <= min_like
            #num_reflections += reflected
            normal = grad_p_x / torch.norm(grad_p_x, dim=-1)
            delta_velocity = 2 * torch.einsum('ai, ai -> a', velocity, normal).reshape(-1, 1) * normal
            velocity[reflected, :] -= delta_velocity[reflected, :]
            self.n_out_steps += reflected.sum()
            self.n_in_steps += (~reflected).sum()


            # if p_x <= min_like:
            #     # if reflected:
            #     #     raise ValueError("Particle got stuck at the boundary")
            #     # Reflect velocity using the normal vector of the wall
            #     normal = grad_p_x / torch.norm(grad_p_x)
            #     velocity -= 2 * torch.dot(velocity, normal) * normal
            #     # reflected = True
            #     self.n_out_steps += 1
            # else:
            #     # reflected = False
            #     self.n_in_steps += 1

        return position, p_x

    def reflect_sampling(self, min_loglike):
        """
        Slice sampling algorithm for PyTorch.

        Arguments:
        log_prob_func -- A function that takes a PyTorch tensor and returns its log probability.
        initial_x -- A PyTorch tensor representing the initial value of x.
        num_samples -- The number of samples to generate.
        step_size -- The step size used in the algorithm.

        Returns:
        samples -- A PyTorch tensor of shape (num_samples,) representing the generated samples.
        """
        cluster_volumes = torch.exp(self.summaries.get_logXp())
        point = self.live_points.get_random_sample(cluster_volumes)
        x = point.get_values()
        label = point.get_labels()

        # subset = self.live_points.label_subset(label)
        # if subset.get_size() == 1:
        #     alpha = 1.0
        # else:
        #     point = subset.get_random_sample(torch.ones(1))
        #     while torch.allclose(point.values, x):
        #         point = subset.get_random_sample(torch.ones(1))
        #     alpha = torch.abs(x - point.get_values())**0.5

        num_steps = self.n_repeats

        #alpha = cluster_volumes[label]**(1/self.nparams)

        log_gamma = torch.lgamma(torch.tensor(self.nparams / 2 + 1))
        # Use the torch.exp function to compute the exponential of the log
        gamma = torch.exp(log_gamma)
        # Use the formula for the radius in terms of volume and dimension
        alpha = (cluster_volumes[label] * gamma / pi ** (self.nparams / 2)) ** (1 / self.nparams)
        alpha = 1.0
        #print(alpha)
        #alpha = self.get_score(x)[1] * 0.05
        #print(alpha, cluster_volumes[label])
        dt = 0.1

        accepted = False
        num_fails = 0
        while not accepted:
            r = torch.randn_like(x)
            velocity = alpha * r #/ torch.norm(r, dim=-1, keepdim=True)
            new_x, new_loglike = self.simulate_particle_in_box(position=x, velocity=velocity, min_like=min_loglike,
                                                               dt=dt, num_steps=num_steps)
            accepted = new_loglike > min_loglike[0]

            #acceptance = self.n_in_steps / (self.n_out_steps + self.n_in_steps)
            #if acceptance > 0.5:
            #     self.dt = clip(1.1 * self.dt, 1e-5, 10.)
            # elif acceptance < 0.2:
            #     self.dt = clip(0.9 * self.dt, 1e-5, 10.)

            if not accepted:
                num_fails += 1
                #x = self.live_points.get_random_sample(self.cluster_volumes).get_values()
                point = self.live_points.get_random_sample(cluster_volumes)
                x = point.get_values()
                label = point.get_labels()

                # Use the formula for the radius in terms of volume and dimension
                # alpha = (cluster_volumes[label] * gamma / pi ** (self.nparams / 2)) ** (1 / self.nparams)

                # subset = self.live_points.label_subset(label)
                # if subset.get_size() == 1:
                #     alpha = 1.0
                # else:
                #     point = subset.get_random_sample(torch.ones(1))
                #     while torch.allclose(point.values, x):
                #         point = subset.get_random_sample(torch.ones(1))
                #     alpha = torch.abs(x - point.get_values())**0.5
                #alpha = cluster_volumes[label]**(1/self.nparams)
                #alpha = self.get_score(x)[1] * 0.05
                #dt = dt*0.5

        assert new_loglike > min_loglike[0], "loglike = {}, min_loglike = {}".format(loglike, min_loglike)

        #print(new_loglike, min_loglike[0])
        sample = NSPoints(self.nparams)
        sample.add_samples(values=new_x.reshape(1, -1),
                           logL=new_loglike.reshape(1),
                           weights=torch.ones(1, device=self.device))
        return sample


    def find_new_sample(self, min_like):
        ''' Sample the prior until finding a sample with higher likelihood than a
        given value
        Parameters
        ----------
          min_like : float
            The threshold log-likelihood
        Returns
        -------
          newsample : pd.DataFrame
            A new sample
        '''
        newlike = -torch.inf
        while newlike < min_like:
            if self.acc_rate_pure_ns > 0.1:
                newsample = self.sample_prior(npoints=1)
                pure_ns = True
            else:
                newsample = self.reflect_sampling(min_like)
                pure_ns = False

            newlike = newsample.get_logL()[0]

            self.n_tries += 1
            if pure_ns: self.n_tries_pure_ns += 1

        if pure_ns:
            self.n_accepted_pure_ns += 1
            self.acc_rate_pure_ns = self.n_accepted_pure_ns / self.n_tries_pure_ns

        return newsample


if __name__ == "__main__":
    ndims = 20
    mvn1 = torch.distributions.MultivariateNormal(loc=2*torch.ones(ndims),
                                                 covariance_matrix=torch.diag(
                                                     0.2*torch.ones(ndims)))

    mvn2 = torch.distributions.MultivariateNormal(loc=-1*torch.ones(ndims),
                                                 covariance_matrix=torch.diag(
                                                     0.2*torch.ones(ndims)))

    true_samples = torch.cat([mvn1.sample((5000,)), mvn2.sample((5000,))], dim=0)

    def get_loglike(theta):
        lp = torch.logsumexp(torch.stack([mvn1.log_prob(theta), mvn2.log_prob(theta)]), dim=0, keepdim=False) - torch.log(torch.tensor(2.0))
        return lp

    params = []

    for i in range(ndims):
        params.append(
            Param(
                name=f'p{i}',
                prior_type='Uniform',
                prior=(-5, 5),
                label=f'p_{i}')
        )

    ns = GaliNest(
        nlive=25*len(params),
        loglike=get_loglike,
        params=params,
        clustering=True,
        tol=1e-1
    )

    ns.run()

    # The true logZ is the inverse of the prior volume
    import numpy as np
    print('True logZ = ', np.log(1 / 10**len(params)))
    print('Number of evaluations', ns.get_like_evals())

    from getdist import plots, MCSamples
    samples = ns.convert_to_getdist()
    true_samples = MCSamples(samples=true_samples.numpy(), names=[f'p{i}' for i in range(ndims)])
    g = plots.get_subplot_plotter()
    g.triangle_plot([true_samples, samples], filled=True, legend_labels=['True', 'GDNest'])
    g.export('test_galilean.png')
