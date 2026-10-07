package com.qianjiangsoft.graphapi;

import com.qianjiangsoft.graphapi.dto.EdgeReturnDTO;
import org.junit.Test;
import org.springframework.boot.test.context.SpringBootTest;

import java.lang.reflect.Field;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;

/**
 * @author ulmarr8861
 * @date 2022/7/13
 */
@SpringBootTest
public class lhTest {
    @Test
    public void test1() throws IllegalAccessException, NoSuchFieldException {
        EdgeReturnDTO edgeReturnDTO = new EdgeReturnDTO();
        edgeReturnDTO.setId("1");
        edgeReturnDTO.setSource("2");
        edgeReturnDTO.setDestination("3");
        Map<String, Object> a = objectToMap(edgeReturnDTO,edgeReturnDTO.getClass().getDeclaredField("destination"));
        System.out.println();
    }



    public static Map<String, Object> objectToMap(Object obj, Field ignoreField) throws IllegalAccessException {
        Map<String, Object> map = new HashMap<>();
        Class<?> clazz = obj.getClass();
        for (Field field : clazz.getDeclaredFields()) {
            if (field.equals(ignoreField)) {
                continue;
            }
            field.setAccessible(true);

            String fieldName = field.getName();
            Object value = field.get(obj);
            map.put(fieldName, value);
        }
        return map;
    }



}
