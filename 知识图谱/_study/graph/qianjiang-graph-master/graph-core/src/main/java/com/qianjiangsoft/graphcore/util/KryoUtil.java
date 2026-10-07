package com.qianjiangsoft.graphcore.util;

import com.esotericsoftware.kryo.Kryo;
import com.esotericsoftware.kryo.io.Input;
import com.esotericsoftware.kryo.io.Output;
import com.esotericsoftware.kryo.serializers.CompatibleFieldSerializer;
import com.esotericsoftware.kryo.util.DefaultInstantiatorStrategy;
import com.esotericsoftware.kryo.util.Pool;
import lombok.extern.slf4j.Slf4j;
import org.objenesis.strategy.StdInstantiatorStrategy;

import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;

/**
 * @author marshallwu
 * @date 2022/7/1 8:21
 */
@Slf4j
public class KryoUtil {

    public static int maximumCapacity = 512;

    private static final Pool<Kryo> kryoPool = new Pool<Kryo>(true, false, maximumCapacity) {

        @Override
        protected Kryo create() {
            Kryo kryo = new Kryo();
            // 关闭序列化注册，会导致性能些许下降，但在分布式环境中，注册类生成ID不一致会导致错误
            kryo.setRegistrationRequired(false);
            // 支持循环引用，也会导致性能些许下降 T_T
            kryo.setReferences(true);
            kryo.setDefaultSerializer(CompatibleFieldSerializer.class);
            kryo.setInstantiatorStrategy(new DefaultInstantiatorStrategy(new StdInstantiatorStrategy()));
            return kryo;
        }
    };

    public static <T> void writeObjectToFile(String filePath, T object) throws FileNotFoundException {
        Kryo kryo = kryoPool.obtain();
        Output output = new Output(new FileOutputStream(filePath), 100 * 1000 * 1000);
        try {
            kryo.writeObject(output, object);
        }catch (Exception e) {
            throw e;
        }finally {
            output.close();
            kryoPool.free(kryo);
        }
    }

    public static <T> T readObjectFromFile(String filePath, Class clazz) throws FileNotFoundException {
        Kryo kryo = kryoPool.obtain();
        Input input = new Input(new FileInputStream(filePath));
        T object = null;
        try {
            object = (T) kryo.readObject(input, clazz);
        }catch (Exception e) {
            throw e;
        }finally {
            input.close();
            kryoPool.free(kryo);
        }

        return object;
    }
}
