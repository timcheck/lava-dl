from syslog import LOG_CONS
import torch


# dendrite_print_time_index = -1
# feedback_print_time_index = -1
spike_print_time_index = 48
def custom_recurrent_ground_truth_1(z, neuron, recurrent_synapse):
    log = []
    # x = torch.zeros_like(z).to(x.device)
    print('custom_recurrent_ground_truth_1')
    x = torch.zeros_like(z).to(z.device)
    spike = torch.zeros(z.shape[:-1]).to(x.device)
    for time in range(z.shape[-1]):
        log_item = {}
        dendrite = z[..., time:time + 1]
        log_item['dendrite'] = dendrite
        # if time == dendrite_print_time_index:
        #     print(f'{dendrite.shape=}')
        #     print(f'{dendrite.squeeze()=}')
        feedback = recurrent_synapse(spike.reshape(dendrite.shape))
        log_item['feedback'] = feedback
        # if time == feedback_print_time_index:
        #     print(f'{feedback.shape=}')
        #     print(f'{feedback.squeeze()=}')
        spike = neuron(dendrite + feedback)
        log_item['spike'] = spike
        if time == spike_print_time_index:
            print(f'{spike.shape=}')
            print(f'{spike.squeeze()=}')
        x[..., time:time + 1] = spike
        log.append(log_item)
    return x, log

def custom_recurrent_ground_truth_2(z, neuron, recurrent_synapse):
    log = []
    print('custom_recurrent_ground_truth_2')
    x = torch.zeros_like(z).to(z.device)
    mat_shape = recurrent_synapse.weight.shape[:2]
    pre_hook = recurrent_synapse.pre_hook_fx
    recurrent_mat = recurrent_synapse.weight.reshape(mat_shape)
    if pre_hook is not None:
        recurrent_mat = pre_hook(recurrent_mat)

    recurrent_mat_T = recurrent_mat.transpose(0, 1)
    spike = torch.zeros(z.shape[:-1] + (1,)).to(x.device)
    for time in range(z.shape[-1]):
        log_item = {}
        dendrite = z[..., time]
        log_item['dendrite'] = dendrite
        # if time == dendrite_print_time_index:
        #     print(f'{dendrite.shape=}')
        #     print(f'{dendrite.squeeze()=}')
        feedback = torch.matmul(spike[..., 0], recurrent_mat_T)
        log_item['feedback'] = feedback
        # if time == feedback_print_time_index:
        #     print(f'{feedback.shape=}')
        #     print(f'{feedback.squeeze()=}')
        spike = neuron(torch.unsqueeze(dendrite + feedback, dim=-1))
        log_item['spike'] = spike
        if time == spike_print_time_index:
            print(f'{spike.shape=}')
            print(f'{spike.squeeze()=}')
        x[..., time:time + 1] = spike
        log.append(log_item)
    return x, log

def custom_recurrent(z, neuron, recurrent_synapse):
    mat_shape = recurrent_synapse.weight.shape[:2]
    pre_hook = recurrent_synapse.pre_hook_fx
    # recurrent_mat = pre_hook(recurrent_synapse.weight.reshape(mat_shape))
    recurrent_mat = recurrent_synapse.weight.reshape(mat_shape)
    if pre_hook is not None:
        recurrent_mat = pre_hook(recurrent_mat)
    return CustomRecurrent.apply(z, neuron, recurrent_mat)


class CustomRecurrent(torch.autograd.Function):
    @staticmethod
    def forward(ctx, z, neuron, recurrent_mat):
        '''
                        ------- R <------
         fb[t] = R s[t] |               |
                        v               |
             z[t]----->(+)---->( N )----|----> s[t]

        '''
        z = z.detach().requires_grad_()
        x = torch.zeros_like(z).to(z.device)
        recurrent_mat_T = recurrent_mat.transpose(0, 1).clone().detach()

        ctx.dend_sums = []
        ctx.spikes = []
        spike = torch.zeros(z.shape[:-1] + (1,)).to(x.device)
        for time in range(z.shape[-1]):
            dendrite = z[..., time]
            feedback = torch.matmul(spike[..., 0], recurrent_mat_T)
            with torch.enable_grad():
                dend_sum = (dendrite + feedback).detach().requires_grad_()
                spike = neuron(torch.unsqueeze(dend_sum, dim=-1))
                ctx.dend_sums.append(dend_sum)
                ctx.spikes.append(spike)
            x[..., time:time + 1] = spike

        ctx.recurrent_mat = recurrent_mat
        ctx.x = x
        return x.detach()

    @staticmethod
    def backward(ctx, grad_x):
        '''
                        ------> R -------
                        |               | grad_fb = (R.T) d/dz[t]
                        |               v
            d/dz[t]<----|----( N )<----(+)---- d/ds[t]
                    grad_dend
        '''
        grad_z = torch.zeros_like(grad_x).to(grad_x.device)
        grad_neuron = None
        grad_spike = 0
        # grad_recurrent_mat = torch.zeros_like(ctx.recurrent_mat).to(grad_x.device)
        for time in range(grad_x.shape[-1])[::-1]:
            grad_spike = grad_spike + grad_x[..., time:time + 1]
            torch.autograd.backward(ctx.spikes[time], grad_spike)
            grad_dend_sum = ctx.dend_sums[time].grad
            grad_feedback = grad_dend_sum
            grad_dendrite = grad_dend_sum
            # if time >= 1:
            #     grad_recurrent_mat += torch.matmul(grad_feedback.transpose(0, 1), ctx.spikes[time - 1][..., 0])
            grad_spike = torch.unsqueeze(torch.matmul(grad_feedback,
                                                      ctx.recurrent_mat),
                                         dim=-1)
            grad_z[..., time] = grad_dendrite

        # Factoring the recurrent gradient computation out of for loop
        # gradW = grad_output x input = grad_feedback x spike
        # grad_output: N, C_out, T => C_out, NT
        # input: N, C_in, T => NT, C_in
        grad_output = grad_z[..., 1:].transpose(
            0, 1).reshape(grad_dendrite.shape[1], -1)
        input = ctx.x[..., :-1].transpose(1, 2).reshape(-1, ctx.x.shape[1])
        # grad_output = grad_z[..., 1:].permute([1, 0, 2]).reshape(grad_dendrite.shape[1], -1)
        # input = ctx.x[..., :-1].permute([0, 2, 1]).reshape(-1, ctx.x.shape[1])
        grad_recurrent_mat = torch.matmul(grad_output, input)

        return grad_z, grad_neuron, grad_recurrent_mat
