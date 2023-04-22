import tensorflow  as tf
import pandas as pd
import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin, ClassifierMixin
from sklearn.metrics import r2_score, accuracy_score
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from scipy.special import expit, softmax

def create_model(inp, o, config, embedding, 
                 l1=0, l2=0,
                 cont_dtype=tf.float32, 
                 ord_dtype=tf.float32):
    if type(inp) == dict:
        X_inp = {}
        X_cat = []
        for k, v in inp.items():
            if v[0] == 'cont':
                X_inp[k] = tf.keras.Input(dtype=cont_dtype, shape=(v[1], ), name=k)
                X_cat.append(X_inp[k])
            elif v[0] == 'ord':
                X_inp[k] = tf.keras.Input(dtype=ord_dtype, shape=(v[1], ), name=k)
                X_cat.append(X_inp[k])
            elif v[0] == 'emb':
                X_inp[k] =tf.keras.Input(dtype=tf.int32, shape=(v[3], ), name=k)
                if v[4] > 0 and v[5] > 0:
                    kreg = tf.keras.regularizers.L1L2(v[4], v[5])
                elif v[4] > 0:
                    kreg  = tf.keras.regularizers.L1(v[4])
                elif v[5] > 0:
                    kreg = tf.keras.regularizers.L2(v[5])
                else:
                    kreg = None
                X_emb = tf.keras.layers.Embedding(v[1], v[2], embeddings_regularizer = kreg,
                                                  dtype=cont_dtype, name=k+'_emb')(X_inp[k])
                X_emb = tf.reshape(X_emb, (-1, v[2] * v[3]), name=k + '_reshape_emb')
                X_cat.append(X_emb)
        X = tf.concat(X_cat, axis=-1, name='concat_inputs')
    else:
        X_inp = tf.keras.Input(dtype=cont_dtype, shape=(inp, ), name='input_layer')
        X = X_inp
    l_cnt = 0
    for i in config:
        l1 = i.get('l1', 0)
        l2 = i.get('l2', 0)
        if l1 > 0 and l2 > 0:
            kreg = tf.keras.regularizers.L1L2(l1, l2)
        elif l1 > 0:
            kreg = tf.keras.regularizers.L1(l1)
        elif l2 > 0:
            kreg = tf.keras.regularizers.L2(l2)
        else:
            kreg = None
        X = tf.keras.layers.Dense(i['unit'], activation=i.get('activation', None), 
                                  kernel_regularizer=kreg,
                                  name='l_{}'.format(l_cnt))(X)
        bn = i.get('batch_norm', False)
        if bn:
            X = tf.keras.layers.BatchNormalization()(X)
        do = i.get('dropout', 0)
        if do > 0:
            X = tf.keras.layers.Dropout(do)(X)
        l_cnt += 1
    if l1 > 0 and l2 > 0:
        kreg = tf.keras.regularizers.L1L2(l1, l2)
    elif l1 > 0:
        kreg = tf.keras.regularizers.L1(l1)
    elif l2 > 0:
        kreg = tf.keras.regularizers.L2(l2)
    else:
        kreg = None
    X = tf.keras.layers.Dense(o, kernel_regularizer=kreg, name='l_output')(X)
    return tf.keras.Model(inputs=X_inp, outputs=X)

def create_dataset(X, y=None, ordinal=None, embedding=None, batch_size=512, shuffle_size=102400):
    output_ = None
    if ordinal is not None or embedding is not None:
        inp, arr = {}, {}
        if type(X) == pd.DataFrame:
            df_cont = X.select_dtypes('float')
            if len(df_cont.columns) > 0:
                inp['X_cont'] =  ('cont', len(df_cont.columns))
                arr['X_cont'] =  df_cont
            if ordinal is not None:
                inp['X_ord'] = ('ord', len(ordinal))
                arr['X_ord'] = X[ordinal]
            if embedding is not None:
                for k, v in enumerate(embedding):
                    inp['X_emb_{}'.format(k)] = ('emb', v[1], v[2], len(v[0]), v[3], v[4])
                    arr['X_emb_{}'.format(k)] = X[v[0]]
        else:
            cont_len = X.shape[-1] - (0 if ordinal is None else ordinal)\
                                    - (0 if embedding is None else np.sum([i[0] for i in embedding]))
            if cont_len > 0:
                inp['X_cont'] = ('cont', cont_len)
                arr['X_cont'] = X[:, :cont_len]
            if ordinal is None:
                ordinal = 0
            if ordinal > 0:
                inp['X_ord'] = ('ord', ordinal)
                arr['X_ord'] = X[:, cont_len: (cont_len + ordinal)]
            if embedding is not None:
                f_ = cont_len + ordinal
                for k,v in enumerate(embedding):
                    inp['X_emb_{}'.format(k)] = ('emb', v[1], v[2], v[0], v[3], v[4])
                    arr['X_emb_{}'.format(k)] = X[:, f_:f_+v[0]]
                    f_ += v[0]
        if y is not None:
            o = 1 if len(y.shape) == 1 else y.shape[-1]
            output_= (inp, o)
            ds = tf.data.Dataset.from_tensor_slices((arr, y))
        else:
            ds = tf.data.Dataset.from_tensor_slices(arr)
    else:
        if y is not None:
            o = 1 if len(y.shape) == 1 else y.shape[-1]
            output_= (X.shape[-1], o)
            ds = tf.data.Dataset.from_tensor_slices((X, y))
        else:
            ds = tf.data.Dataset.from_tensor_slices(X)
    if shuffle_size > 0:
        ds = ds.shuffle(shuffle_size)
    if batch_size > 0:
        ds = ds.batch(batch_size)
    return ds, output_
    
class NNEstimator(BaseEstimator):
    """
    Neural Network Estimator with tensorflow
    """
    def __init__(self, network_config, ordinal=None, embedding=None, 
                epochs=10, batch_size=512, shuffle_size=102400, learning_rate=0.001,
                l1=0,
                l2=0,
                early_stopping=None,
                reduce_lr_on_plateau=None,
                lr_scheduler=None,
                verbose=0,
                validation_fraction = 0,
                random_state=0):
        """
            Neural Network Estimator with tensorflow
            Parameters:
                network_config: list
                    neural network configuration 
        """
        self.model_ = None
        self.network_config = network_config
        self.ordinal = ordinal
        self.embedding = embedding
        self.epochs = epochs
        self.batch_size = batch_size
        self.shuffle_size = shuffle_size
        self.learning_rate = learning_rate
        self.l1 = l1
        self.l2 = l2
        self.early_stopping = early_stopping
        self.reduce_lr_on_plateau = reduce_lr_on_plateau
        self.lr_scheduler = lr_scheduler
        self.verbose = verbose
        self.random_state = random_state
        self.validation_fraction = validation_fraction
        
    def get_params(self, deep=True):
        return {'network_config': self.network_config, 
                'ordinal': self.ordinal,
                'embedding': self.embedding,
                'epochs': self.epochs,
                'batch_size': self.batch_size,
                'shuffle_size': self.shuffle_size,
                'learning_rate': self.learning_rate,
                'l1': self.l1,
                'l2': self.l2,
                'early_stopping': self.early_stopping,
                'reduce_lr_on_plateau': self.reduce_lr_on_plateau,
                'lr_scheduler': self.lr_scheduler,
                'validation_fraction': self.validation_fraction, 
                'random_state': self.random_state,
                'verbose': self.verbose
               }
    
    def set_params(self, **params):
        self.network_config = params.get('network_config', self.network_config)
        self.ordinal = params.get('ordinal', self.ordinal)
        self.embedding = params.get('embedding', self.embedding)
        self.epochs = params.get('epochs', self.epochs)
        self.batch_size = params.get('batch_size', self.batch_size)
        self.shuffle_size = params.get('shuffle_size', self.shuffle_size)
        self.learning_rate = params.get('learning_rate', self.learning_rate)
        self.l1 = params.get('l1', self.l1)
        self.l2 = params.get('l2', self.l2)
        self.early_stopping = params.get('early_stopping', self.early_stopping)
        self.reduce_lr_on_plateau = params.get('reduce_lr_on_plateau', self.reduce_lr_on_plateau)
        self.validation_fraction =  params.get('validation_fraction', self.validation_fraction)
        self.lr_scheduler = params.get('lr_scheduler', self.lr_scheduler)
        self.verbose=params.get('verbose', self.verbose)
        if self.model_ is not None:
            self.model_ = None
            tf.keras.backend.clear_session()
    
    def model_summary(self):
        self.model_.summary()
    
    def fit_(self, X, y, loss, num_label=0):
        if self.model_ is not None:
            self.model_ = None
        if self.random_state > 0:
            tf.random.set_seed(self.random_state)
        tf.keras.backend.clear_session()
        cb = []
        if self.validation_fraction > 0:
            assert self.validation_fraction < 1.0
            if type(loss) != tf.keras.losses.MeanSquaredError:
                X, X_ev, y, y_ev = train_test_split(X, y, test_size = self.validation_fraction, 
                                                    random_state=self.random_state, stratify=y)
            else:
                X, X_ev, y, y_ev = train_test_split(X, y, test_size = self.validation_fraction, 
                                                    random_state=self.random_state)
            eval_set = (X_ev, y_ev)
        else:
            eval_set = None
        if self.early_stopping is not None:
            cb.append(tf.keras.callbacks.EarlyStopping(**self.early_stopping))
        if self.reduce_lr_on_plateau is not None:
            cb.append(tf.keras.callbacks.ReduceLROnPlateau(**self.reduce_lr_on_plateau))
        if self.lr_scheduler is not None:
            cb.append(tf.keras.callbacks.LearningRateScheduler(self.lr_scheduler, verbose=self.verbose))
        ds_, (inp, o) = create_dataset(X, y, self.ordinal, self.embedding, self.batch_size, self.shuffle_size)
        if eval_set is not None:
            ds_eval_, _ = create_dataset(eval_set[0], eval_set[1], 
                                         self.ordinal, self.embedding, self.batch_size, shuffle_size=0)
        else:
            ds_eval_ = None
        if type(loss) == tf.keras.losses.SparseCategoricalCrossentropy:
            o = o * num_label
        self.model_ = create_model(inp, o, self.network_config, self.embedding)
        self.model_.compile(
            optimizer=tf.keras.optimizers.Adam(learning_rate=self.learning_rate), 
            loss=loss
        )
        history = self.model_.fit(ds_, epochs=self.epochs, 
                                  validation_data=ds_eval_, verbose=self.verbose,
                                  callbacks=cb)
        self.history = history.history
        tf.keras.backend.clear_session()
            
class NNRegressor(NNEstimator, RegressorMixin):
    def fit(self, X, y):
        super().fit_(X, y, loss=tf.keras.losses.MeanSquaredError())
        return self
    
    def predict(self, X):
        ds_, _ = create_dataset(X, ordinal=self.ordinal, 
                                embedding=self.embedding, batch_size=self.batch_size, shuffle_size=0)
        return np.squeeze(self.model_.predict(ds_, verbose=self.verbose))
    
    def score(self, X, y, sample_weight=None):
        return r2_score(y, self.predict(X))
        
class NNClassifier(NNEstimator, ClassifierMixin):
    def fit(self, X, y, eval_set=None):
        self.is_binary = True
        if len(y.shape) == 1:
            self.le = LabelEncoder()
            y_lbl = self.le.fit_transform(y)
            self.is_binary = len(self.le.classes_) == 2
        else:
            self.le = [LabelEncoder() for i in range(y.shape[-1])]
            if type(y) == np.ndarray:
                y_lbl = [le.fit_transform(y[:, i]) for i, le in enumerate(self.le)]
            else:
                y_lbl = [le.fit_transform(y.iloc[:, i]) for i, le in enumerate(self.le)]
            for le_ in self.le:
                if len(le_.classes_) > 2:
                    self.is_binary = False
            y_lbl = np.vstack(y_lbl).T
        if self.is_binary:
            super().fit_(X, y_lbl, loss=tf.keras.losses.BinaryCrossentropy(from_logits=True))
        else:
            if type(self.le) == list:
                raise Exception('Only one target value in case two more label classes')
            super().fit_(X, y_lbl, 
                         loss=tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True), 
                         num_label=len(self.le.classes_))
    
    def predict(self, X):
        ds_, _ = create_dataset(X, ordinal=self.ordinal, 
                                embedding=self.embedding, batch_size=self.batch_size, shuffle_size=0)
        if type(self.le) == list:
            prd = self.model_.predict(ds_, verbose=self.verbose)
            return np.vstack([le.inverse_transform(
                        np.where(prd[:, i] > 0, 1, 0)
                    ) 
                    for i, le in enumerate(self.le)]).T
        else:
            if self.is_binary:
                return self.le.inverse_transform(
                    np.where(np.squeeze(self.model_.predict(ds_, verbose=self.verbose)) > 0, 1, 0)
                )
            else:
                return self.le.inverse_transform(np.argmax(
                    self.model_.predict(ds_, verbose=self.verbose), axis=-1
                ))
    
    def predict_proba(self, X):
        ds_, _ = create_dataset(X, ordinal=self.ordinal, 
                                embedding=self.embedding, batch_size=self.batch_size, shuffle_size=0)
        if type(self.le) == list:
            prd = self.model_.predict(ds_, verbose=self.verbose)
            ret = []
            for i in range(len(self.le)):
                prob = expit(prd[:, i])
                ret.append(np.vstack([1 - prob, prob]).T)
            return ret
        else:
            if self.is_binary:
                prob = expit(np.squeeze(self.model_.predict(ds_, verbose=self.verbose)))
                return np.vstack([1 - prob, prob]).T
            else:
                return softmax(self.model_.predict(ds_, verbose=self.verbose), axis=-1)

    def decision_function(self, X):
        return self.predict(X)

    def score(self, X, y, sample_weight=None):
        if len(y.shape) == 1:
            return accuracy_score(y, self.predict(X))
        else:
            if type(y) == pd.DataFrame:
                y = y.values
            return accuracy_score(y.ravel(), self.predict(X).ravel())