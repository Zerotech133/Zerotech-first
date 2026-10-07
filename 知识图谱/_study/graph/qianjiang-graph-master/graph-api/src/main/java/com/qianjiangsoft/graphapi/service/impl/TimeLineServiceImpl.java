package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollectionUtil;
import cn.hutool.core.convert.Convert;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.util.StrUtil;
import com.qianjiangsoft.graphcore.constants.ConditionType;
import com.qianjiangsoft.graphapi.dto.TimeLineEventDTO;
import com.qianjiangsoft.graphcore.domain.entity.TimeLineEvent;
import com.qianjiangsoft.graphapi.helper.InfluxDBHelper;
import com.qianjiangsoft.graphcore.param.Condition;
import com.qianjiangsoft.graphapi.param.QueryEventListParam;
import com.qianjiangsoft.graphapi.param.TimeLineEventParam;
import com.qianjiangsoft.graphapi.service.TimeLineService;
import org.influxdb.InfluxDB;
import org.influxdb.dto.BatchPoints;
import org.influxdb.dto.Point;
import org.influxdb.dto.Query;
import org.influxdb.dto.QueryResult;
import org.springframework.beans.factory.InitializingBean;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * @author marshallwu
 * @date 2022/4/25 9:14
 */
public class TimeLineServiceImpl implements TimeLineService , InitializingBean {

    InfluxDB influxDB;

    public static String databaseName = "timeline";


    @Override
    public void importEventData(List<TimeLineEventParam> paramList) {

        BatchPoints.Builder builder = BatchPoints.builder();
        for(TimeLineEventParam eventParam : paramList) {
            TimeLineEvent timeLineEvent = BeanUtil.copyProperties(eventParam, TimeLineEvent.class);
            Point point = Point.measurement(InfluxDBHelper.generateTableName(timeLineEvent.getSpaceId()))
                    .time(timeLineEvent.getTime().getTime(), TimeUnit.MILLISECONDS)
                    .addFieldsFromPOJO(timeLineEvent).build();
            builder.point(point);
        }
        influxDB.write(builder.build());
    }


    @Override
    public List<TimeLineEventDTO> queryEventList(QueryEventListParam param) {
        String template = "select time,type,oppositeName,oppositeAccount,spaceId,dataId from {} where caseId='{}'";
        String sql = StrUtil.format(template, InfluxDBHelper.generateTableName(param.getSpaceId()),
                param.getSpaceId());

        if(CollectionUtil.isNotEmpty(param.getTypeList())) {
            sql = sql + " and (1=2 ";
            for(String str : param.getTypeList()) {
                sql = sql + "or type = '" + str + "' ";
            }
            sql = sql + " ) ";
        }

        Condition timeCondition = param.getTimeCondition();
        if(timeCondition != null && timeCondition.getConditionType() != null) {
            if(timeCondition.getConditionType().equals(ConditionType.BEFORE)) {
                sql = sql + "and time < " + DateUtil.parse(param.getTimeCondition().getValue()).millisecond();
            }else if(timeCondition.getConditionType().equals(ConditionType.AFTER)) {
                sql = sql + "and time > " + DateUtil.parse(param.getTimeCondition().getValue()).millisecond();
            }else {
                sql = sql + "and time > " + DateUtil.parse(param.getTimeCondition().getValue()).millisecond()
                        + " and time < " + DateUtil.parse(param.getTimeCondition().getValue2()).millisecond();
            }
        }

        List<QueryResult.Series> seriesList = influxDB.query(new Query(sql, databaseName), TimeUnit.MILLISECONDS)
                .getResults().get(0).getSeries();

        List<TimeLineEvent> timeLineEventList = new ArrayList<>();

        if(seriesList != null) {
            for (List<Object> value : seriesList.get(0).getValues()) {
                TimeLineEvent timeLineEvent = new TimeLineEvent();

                timeLineEvent.setTime(DateUtil.date(Convert.toLong(value.get(0))));
                timeLineEvent.setType(value.get(1).toString());
                timeLineEvent.setOppositeName(value.get(2).toString());
                timeLineEvent.setOppositeAccount(value.get(3).toString());
                timeLineEvent.setSpaceId(value.get(4).toString());
                timeLineEvent.setDataId(value.get(5).toString());
                timeLineEventList.add(timeLineEvent);
            }
        }

        //格式化数据
        Map<String, TimeLineEventDTO> timeLineEventDTOMap = new HashMap<>();
        for(TimeLineEvent timeLineEvent : timeLineEventList) {
            String timeStr = DateUtil.format(timeLineEvent.getTime(), "yyyyMMddHH");
            String key = StrUtil.join(StrUtil.UNDERLINE, timeStr, timeLineEvent.getType(),
                    timeLineEvent.getOppositeAccount());

            TimeLineEventDTO timeLineEventDTO = timeLineEventDTOMap.get(key);
            if(timeLineEventDTO == null) {
                timeLineEventDTO = BeanUtil.copyProperties(timeLineEvent, TimeLineEventDTO.class);
                timeLineEventDTO.setCount(1L);
                timeLineEventDTOMap.put(key, timeLineEventDTO);
            }else {
                timeLineEventDTO.setCount(timeLineEventDTO.getCount() + 1);
            }
        }

        return new ArrayList<>(timeLineEventDTOMap.values());
    }

    @Override
    public void clearCaseEventData(String spaceId) {
        String sql = "DROP MEASUREMENT {} ";
        influxDB.query(new Query(StrUtil.format(sql, InfluxDBHelper.generateTableName(spaceId))));
    }


    @Override
    public void afterPropertiesSet() throws Exception {
        influxDB.setDatabase(databaseName)
                .enableBatch(100,1000, TimeUnit.MILLISECONDS);
    }
}
