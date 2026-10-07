package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.bean.BeanUtil;
import com.qianjiangsoft.graphapi.dto.CardListDTO;
import com.qianjiangsoft.graphapi.dto.CreateCardDTO;
import com.qianjiangsoft.graphapi.service.CardService;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.model.Card;
import com.qianjiangsoft.graphcore.param.card.CreateCardParam;
import com.qianjiangsoft.graphcore.service.SpaceCardService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/12/1 10:51
 */
@Service
public class CardServiceImpl implements CardService {

    @Autowired
    SpaceCardService spaceCardService;

    @Override
    public CreateCardDTO createCard(CreateCardParam param) {
        Card card = spaceCardService.createCard(param);

        return BeanUtil.copyProperties(card, CreateCardDTO.class);
    }

    @Override
    public List<CardListDTO> selectCardList() {
        List<Card> cardList = spaceCardService.selectCardListBySpaceId(SpaceContextHolder.getSpaceId());
        return BeanUtil.copyToList(cardList, CardListDTO.class);
    }

    @Override
    public void deleteAll() {
        spaceCardService.deleteCardBySpaceId(SpaceContextHolder.getSpaceId());
    }
}
