import tensorflow as tf
ds = tf.contrib.distributions

class RNNObserver():
    """Processes observations of another agent with RNN to create 'belief state'
    over goal. Own state plus RNN output is fed into two networks to produce
    policy and value function. Trained with REINFORCE."""
    
    def __init__(self, alice_env, bob_env, shared_layer_sizes = [],
                 policy_layer_sizes = [], value_layer_sizes = [], use_RNN = True):
      
      self.use_RNN = use_RNN             
      self.obs_states = tf.placeholder(tf.int32, [None], name = "observed_states")
      self.obs_actions = tf.placeholder(tf.int32, [None], name = "observed_actions")
      self.state = tf.placeholder(tf.int32, [], name = 'self_state')
      self.action = tf.placeholder(tf.int32, [], name = 'self_action')
      self.return_estimate = tf.placeholder(tf.float32, [], name = "return_estimate")
      self.learning_rate = tf.placeholder(tf.float32, [], name = "learning_rate")          
      self.entropy_scale = tf.placeholder(tf.float32, [], name = "entropy_scale")
      self.value_scale = tf.placeholder(tf.float32, [], name = "value_scale")

      # rnn processing observations of other agent behaving
      if self.use_RNN:
        with tf.variable_scope('rnn'):
            rnn_inputs = tf.one_hot(self.obs_states * alice_env.nA + self.obs_actions,
                                    depth = alice_env.nA * alice_env.nS) # one-hot of (s,a)
            cell = tf.contrib.rnn.GRUCell(1) # scalar core state
            # rnn_inputs must be 1 X t x d, where d = nS*nA
            _, z = tf.nn.dynamic_rnn(cell,
                                     tf.expand_dims(rnn_inputs,0),
                                     dtype = tf.float32)
            # rnn output should be 1 x 1
            self.z = tf.squeeze(z, axis = 0) # drop batch_size=1 dim
      else:
        self.z = tf.placeholder(tf.float32, [1], name = "goal_belief")
          
      # concat agent state and rnn output as input to policy/value heads
      one_hot_state = tf.one_hot(self.state, depth = bob_env.nS)
      x = tf.expand_dims(tf.concat([one_hot_state, self.z], axis = 0), 0)
      
      # shared layers (fully-connected MLP)
      with tf.variable_scope('shared'):
        i = 1
        for n in shared_layer_sizes:
          x = tf.layers.dense(x, n,
                              activation = tf.nn.relu,
                              name = 'layer_%i' % i,
                              kernel_initializer = tf.contrib.layers.variance_scaling_initializer())
          i += 1 
          
      # value head (fully-connected MLP)
      with tf.variable_scope('value'):
        x_value = x # [=] 1 x (nS+1) or 1 x (shared_layer_sizes[-1])
        i = 1
        for n in value_layer_sizes:
          x_value = tf.layers.dense(x_value, n,
                                    activation = tf.nn.relu,
                                    name = 'layer_%i' % i,
                                    kernel_initializer = tf.contrib.layers.variance_scaling_initializer())
          i += 1
        self.value = tf.squeeze(tf.layers.dense(x_value, 1,
                                                activation = None,
                                                name = 'layer_%i' % i))
        self.advantage = self.return_estimate - tf.stop_gradient(self.value)
        self.value_loss = self.value_scale * tf.squared_difference(self.value, self.return_estimate)
          
      # policy head (fully-connected MLP)
      with tf.variable_scope('policy'):
        x_policy = x # [=] 1 x (nS+1) or 1 x (shared_layer_sizes[-1])
        i = 1
        for n in policy_layer_sizes:
          x_policy = tf.layers.dense(x_policy, n,
                                     activation = tf.nn.relu,
                                     name = 'layer_%i' % i,
                                     kernel_initializer = tf.contrib.layers.variance_scaling_initializer())
          i += 1
        self.action_logits = tf.squeeze(tf.layers.dense(x_policy, bob_env.nA,
                                                        activation = None,
                                                        name = 'layer_%i' % i), axis = 0)
        self.action_probs = tf.nn.softmax(self.action_logits)
        self.picked_action_prob = tf.gather(self.action_probs, self.action)
        self.action_entropy = ds.Categorical(probs = self.action_probs).entropy()
        self.pg_loss = -tf.log(self.picked_action_prob) * self.advantage
        self.ent_loss =  -self.entropy_scale * self.action_entropy
      
      # total loss and train op
      self.loss = self.pg_loss + self.ent_loss + self.value_loss
      self.optimizer = tf.train.AdamOptimizer(learning_rate = self.learning_rate)
      self.train_op = self.optimizer.minimize(self.loss, global_step = tf.contrib.framework.get_global_step())
            
    def get_z(self, obs_states, obs_actions, sess = None):
        sess = sess or tf.get_default_session()
        feed_dict = {self.obs_states: obs_states,
                     self.obs_actions: obs_actions}
        return sess.run(self.z, feed_dict)
    
    def predict(self, state, obs_states = None, obs_actions = None, z = None,
                sess = None):
      '''If use_RNN, must provide state, obs_states, and obs_actions.
         Else, must provide state and z.'''
      sess = sess or tf.get_default_session()
      if self.use_RNN:
        feed_dict = {self.obs_states: obs_states,
                     self.obs_actions: obs_actions,
                     self.state: state}
      else:
        feed_dict = {self.state: state,
                     self.z: z}
      return sess.run([self.action_probs, self.value, self.z, self.action_logits], feed_dict)

    def update(self, state, action, return_estimate,
               learning_rate, entropy_scale, value_scale,
               obs_states = None, obs_actions = None, z = None,
               sess = None):
        sess = sess or tf.get_default_session()
        if self.use_RNN:
          feed_dict = {self.obs_states: obs_states,
                       self.obs_actions: obs_actions,
                       self.state: state,
                       self.action: action,
                       self.return_estimate: return_estimate,
                       self.learning_rate: learning_rate,
                       self.entropy_scale: entropy_scale,
                       self.value_scale: value_scale}
        else:
          feed_dict = {self.z: z,
                       self.state: state,
                       self.action: action,
                       self.return_estimate: return_estimate,
                       self.learning_rate: learning_rate,
                       self.entropy_scale: entropy_scale,
                       self.value_scale: value_scale}
        _, loss = sess.run([self.train_op, self.loss], feed_dict)
        return loss